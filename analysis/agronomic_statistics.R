#!/usr/bin/env Rscript
# Agronomic statistics for a randomised complete block design (base R only).
#
#   Rscript analysis/agronomic_statistics.R [--plots examples/agronomic_plots.example.csv] [--out results/analytical]
#
# Method (unchanged from the source repository): 3 blocks x 2 crops x 2 treatments
# (blockchain_iot vs traditional), one plot per block-crop-treatment cell. Per crop and response the
# randomised-block model  y ~ block + treatment  is fitted; the treatment F test has df (1, 2).
# Responses are per hectare: yield_kg_ha, water_m3_ha (scheduled volumes), labour_h_ha, labour_cost_usd_ha.
#
# The default input is ILLUSTRATIVE plot data invented for this package. Results computed from it
# demonstrate the method only. They are not, and must not be presented as, a reproduction of the
# field's yield, water or labour results (40.4 % / 34.6 % water, 33.9 % labour, +2.2 % / +3.3 %
# yield), which are field-measured and need restricted plot records.
#
# Outputs: <out>/agronomic_means_example.csv, <out>/agronomic_rcbd_example.csv (provenance column
# propagated from the input).

args <- commandArgs(trailingOnly = TRUE)
opt <- function(name, default) {
  i <- match(paste0("--", name), args)
  if (is.na(i)) default else args[i + 1]
}
root <- local({
  f <- sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))
  if (length(f)) normalizePath(file.path(dirname(f), "..")) else getwd()
})
p <- function(...) file.path(root, ...)
plots_path <- opt("plots", p("examples/agronomic_plots.example.csv"))
out_dir <- opt("out", p("results/analytical"))

plots <- read.csv(plots_path, stringsAsFactors = FALSE, comment.char = "#")
provenance <- if ("provenance" %in% names(plots)) paste(unique(plots$provenance), collapse = "|") else "unknown"

# ------------------------------------------------------------------ design verification
need <- c("plot_id", "block_id", "crop", "treatment", "area_ha", "yield_kg", "water_m3", "labour_h", "labour_cost_usd")
missing <- setdiff(need, names(plots))
if (length(missing)) stop("input lacks columns: ", paste(missing, collapse = ", "))
cells <- table(plots$crop, plots$block_id, plots$treatment)
if (any(cells != 1)) stop("design check failed: every crop x block x treatment cell must hold exactly one plot")
if (!all(sort(unique(plots$treatment)) == c("blockchain_iot", "traditional")))
  stop("design check failed: treatments must be blockchain_iot and traditional")
cat(sprintf("design: %d plots, %.2f ha, %d blocks x %d crops x 2 treatments OK (input provenance: %s)\n",
            nrow(plots), sum(plots$area_ha), length(unique(plots$block_id)), length(unique(plots$crop)), provenance))

# ------------------------------------------------------------------ per-hectare responses
plots$yield_kg_ha <- plots$yield_kg / plots$area_ha
plots$water_m3_ha <- plots$water_m3 / plots$area_ha
plots$labour_h_ha <- plots$labour_h / plots$area_ha
plots$labour_cost_usd_ha <- plots$labour_cost_usd / plots$area_ha
plots$treatment <- factor(plots$treatment, levels = c("traditional", "blockchain_iot"))
plots$block <- factor(plots$block_id)
responses <- c("yield_kg_ha", "water_m3_ha", "labour_h_ha", "labour_cost_usd_ha")

# ------------------------------------------------------------------ means
means <- do.call(rbind, lapply(split(plots, list(plots$crop, plots$treatment)), function(d) {
  do.call(rbind, lapply(responses, function(r) data.frame(
    crop = d$crop[1], treatment = as.character(d$treatment[1]), variable = r, n = nrow(d),
    mean = round(mean(d[[r]]), 3), sd = round(sd(d[[r]]), 3))))
}))
means$provenance <- provenance

# ------------------------------------------------------------------ RCBD models
res <- do.call(rbind, lapply(split(plots, plots$crop), function(d) {
  do.call(rbind, lapply(responses, function(r) {
    fit <- lm(as.formula(paste(r, "~ block + treatment")), data = d)
    a <- anova(fit)
    ci <- confint(fit, "treatmentblockchain_iot", level = 0.95)
    ctrl <- mean(d[[r]][d$treatment == "traditional"])
    biot <- mean(d[[r]][d$treatment == "blockchain_iot"])
    data.frame(crop = d$crop[1], variable = r, traditional_mean = round(ctrl, 3), blockchain_iot_mean = round(biot, 3),
               difference = round(biot - ctrl, 3), difference_pct = round(100 * (biot - ctrl) / ctrl, 2),
               F_treatment = round(a["treatment", "F value"], 3), df_treatment = a["treatment", "Df"],
               df_residual = a["Residuals", "Df"], p_value = signif(a["treatment", "Pr(>F)"], 4),
               F_block = round(a["block", "F value"], 3), p_block = signif(a["block", "Pr(>F)"], 4),
               ci95_low = round(ci[1], 3), ci95_high = round(ci[2], 3))
  }))
}))
res$provenance <- provenance
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
write.csv(means, file.path(out_dir, "agronomic_means_example.csv"), row.names = FALSE)
write.csv(res, file.path(out_dir, "agronomic_rcbd_example.csv"), row.names = FALSE)
cat(sprintf("[RCBD on %s plot data] not a reproduction of field results\n", provenance))
print(res[, c("crop", "variable", "traditional_mean", "blockchain_iot_mean", "difference_pct", "F_treatment",
              "df_treatment", "df_residual", "p_value")], row.names = FALSE)
