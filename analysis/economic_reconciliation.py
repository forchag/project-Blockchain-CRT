#!/usr/bin/env python3
"""Economic formulas: annual benefit, ROI, payback and NPV from public assumptions.

Per season (treated minus control):
  water benefit   = water_saved_m3 x tariff (TND/m3) / fx (TND per USD)
  labour benefit  = labour_saved_usd
  yield benefit   = yield_gain_usd
Annual:
  gross           = (water + labour + yield) x seasons_per_year
  net             = gross - opex_annual
  ROI             = net / capex x 100
  payback         = capex / net                          [years]
  NPV(r, n)       = -capex + sum_{t=1..n} net / (1 + r)^t

Inputs: config/economics.example.yaml. The tariff, exchange rate, discount rate, horizon, seasons,
capex and opex are public assumptions. The season inputs (water, labour, yield differences) are
FIELD-MEASURED in the manuscript and need restricted records; the example file carries
ILLUSTRATIVE values, so every output here is "derived from public tariff assumptions" applied to
illustrative inputs, not a reproduction of the field cost observation (ROI 99 %, payback 1.01 yr).

The one check that needs no field data: ROI and payback published together must satisfy
payback = 1 / (ROI / 100). 1 / 0.99 = 1.0101 yr, consistent with the reported 1.01 yr.

    python3 analysis/economic_reconciliation.py [--config config/economics.example.yaml]
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import FIELD, RESULTS, derived, load_yaml, path, print_record, record, write_csv  # noqa: E402

LABEL_ILLUSTRATIVE_RUN = derived("public tariff assumptions")
DEFAULT_CONFIG = path("config", "economics.example.yaml")


def annuity_factor(r: float, n: int) -> float:
    return sum(1 / (1 + r) ** t for t in range(1, n + 1))


def evaluate(cfg: dict) -> dict:
    s, w = cfg["season_inputs"], cfg["water"]
    water_usd = s["water_saved_m3"] * w["tariff_tnd_per_m3"] / w["fx_tnd_per_usd"]
    f = cfg["seasons_per_year"]
    water, labour, yld = water_usd * f, s["labour_saved_usd"] * f, s["yield_gain_usd"] * f
    gross = water + labour + yld
    net = gross - cfg["opex_annual_usd"]
    capex, r, n = cfg["capex_usd"], cfg["discount_rate"], cfg["horizon_years"]
    npv = -capex + net * annuity_factor(r, n)
    return dict(tariff_tnd_per_m3=w["tariff_tnd_per_m3"], fx_tnd_per_usd=w["fx_tnd_per_usd"], discount_rate=r,
                horizon_years=n, seasons_per_year=f, capex_usd=capex, opex_annual_usd=cfg["opex_annual_usd"],
                annual_water_benefit_usd=round(water, 2), annual_labour_benefit_usd=round(labour, 2),
                annual_yield_benefit_usd=round(yld, 2), gross_annual_benefit_usd=round(gross, 2),
                net_annual_benefit_usd=round(net, 2), roi_pct=round(net / capex * 100, 2),
                payback_years=round(capex / net, 4) if net > 0 else None, npv_usd=round(npv, 2),
                season_inputs_provenance=s.get("provenance", "unspecified"))


def cases(base: dict):
    def mod(name, fn):
        c = copy.deepcopy(base)
        fn(c)
        return name, c
    yield "base", base
    for t in base["water"]["tariff_sensitivity_tnd_per_m3"]:
        yield mod(f"tariff_{t:.3f}_tnd_m3", lambda c, t=t: c["water"].update(tariff_tnd_per_m3=t))
    for r in base.get("discount_sensitivity", []):
        yield mod(f"discount_{int(round(r * 100))}pct", lambda c, r=r: c.update(discount_rate=r))
    yield mod("labour_excluded", lambda c: c["season_inputs"].update(labour_saved_usd=0.0))
    yield mod("one_season_per_year", lambda c: c.update(seasons_per_year=1))


def results(cfg: dict | None = None) -> tuple[list[dict], list[dict]]:
    base = cfg or load_yaml(DEFAULT_CONFIG)
    table = [dict(case=name, **evaluate(c), label=LABEL_ILLUSTRATIVE_RUN) for name, c in cases(base)]
    b = table[0]
    note = ("illustrative season inputs; not a reproduction of the field cost observation"
            if b["season_inputs_provenance"] == "illustrative" else "season inputs supplied by the user")
    out = [
        record("economics.example_roi_pct", "ROI with the example season inputs", b["roi_pct"], "%",
               LABEL_ILLUSTRATIVE_RUN, "net / capex", note),
        record("economics.example_payback_years", "Payback with the example season inputs", b["payback_years"], "yr",
               LABEL_ILLUSTRATIVE_RUN, "capex / net", note),
        record("economics.example_npv_usd", "5-year NPV at 8 % with the example season inputs", b["npv_usd"], "USD",
               LABEL_ILLUSTRATIVE_RUN, "-capex + net x annuity factor", note),
        record("economics.annuity_factor_8pct_5y", "Annuity factor, 8 %, 5 years",
               round(annuity_factor(base["discount_rate"], base["horizon_years"]), 6), "", derived("public tariff assumptions"),
               "sum 1/(1+r)^t, t = 1..5"),
    ]
    rep = base.get("manuscript_reported")
    if rep:
        out += [
            record("economics.reported_roi_pct", "Manuscript ROI", rep["roi_pct"], "%", derived(FIELD, "public tariff"),
                   "field water/labour/yield + public tariff", "reported; needs restricted field inputs to reproduce"),
            record("economics.reported_payback_years", "Manuscript payback", rep["payback_years"], "yr",
                   derived(FIELD, "public tariff"), "field inputs + public tariff",
                   "reported; needs restricted field inputs to reproduce"),
            record("economics.payback_implied_by_reported_roi", "Payback implied by the reported ROI (1 / ROI)",
                   round(100 / rep["roi_pct"], 4), "yr", derived(FIELD, "public tariff"),
                   "payback = capex / net = 1 / (ROI/100)", "consistency check of two published numbers"),
            record("economics.net_benefit_implied_by_reported_roi", "Net annual benefit implied by the reported ROI",
                   round(rep["roi_pct"] / 100 * base["capex_usd"], 2), "USD/yr", derived(FIELD, "public tariff"),
                   "ROI x capex ($3,540)", "implied, not observed"),
        ]
    return out, table


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", default=os.path.join(RESULTS, "analytical", "economic_cases.csv"))
    a = ap.parse_args()
    out, table = results(load_yaml(a.config))
    for r in out:
        print_record(r)
    print()
    for r in table:
        print(f"[{r['label']}] {r['case']:24s} ROI {r['roi_pct']:8.2f} %  payback {r['payback_years']}  NPV {r['npv_usd']:10.2f} USD"
              f"  (season inputs: {r['season_inputs_provenance']})")
    print("wrote", write_csv(table, a.out))


if __name__ == "__main__":
    main()
