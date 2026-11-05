"""Independent-erasure recovery model and structured single-channel loss. All [analytical]."""
import pytest

import packet_loss_recovery as plr
from common import ANALYTICAL


@pytest.mark.parametrize("p,q,reading", [(0.10, 97.2, 86.8), (0.20, 89.6, 57.7), (0.30, 78.4, 29.6)])
def test_manuscript_headline_values(p, q, reading):
    assert 100 * plr.p_quantity(p) == pytest.approx(q, abs=1e-9)
    assert round(100 * plr.p_five(p), 1) == reading


@pytest.mark.parametrize("p", [0.0, 0.01, 0.05, 0.1, 0.3, 0.5, 0.9])
def test_formula_identities(p):
    assert plr.p_quantity(p) == pytest.approx((1 - p) ** 3 + 3 * p * (1 - p) ** 2)
    assert plr.p_five(p) == pytest.approx(plr.p_quantity(p) ** 5)
    assert plr.p_quantity_with_retries(p, 0) == pytest.approx(plr.p_quantity(p))
    r1, r2 = plr.p_quantity_with_retries(p, 1), plr.p_quantity_with_retries(p, 2)
    assert plr.p_quantity(p) - 1e-12 <= r1 <= r2 + 1e-12 <= 1 + 1e-12


def test_retry_model_against_monte_carlo():
    import random
    rng, p, n, ok = random.Random(7), 0.3, 200_000, 0
    for _ in range(n):
        held = sum(rng.random() > p for _ in range(3))
        if held < 2:  # one retransmission round of the missing residues
            held += sum(rng.random() > p for _ in range(3 - held))
        ok += held >= 2
    assert ok / n == pytest.approx(plr.p_quantity_with_retries(p, 1), abs=0.004)


def test_structured_single_channel_loss_is_total_recovery():
    for ch in range(3):
        assert plr.structured_channel_loss_recovery(ch) == 1.0


def test_results_are_labelled_analytical():
    rs = plr.results()
    assert {r["label"] for r in rs} == {ANALYTICAL}
    assert {r["id"] for r in rs} >= {"recovery.per_quantity_10pct", "recovery.per_reading_30pct",
                                     "recovery.structured_single_channel_pct"}
