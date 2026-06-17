# CALIBRATION_VALIDATION_REPORT
**R-1 Draw Calibration Work Package — validation only**  
**Generated:** 2026-06-16T06:44:35.731383+00:00  
**Roadmap phase:** SHADOW_HARDENING  
**Default calibration_mode (live pipeline):** `identity` (unchanged)  

---

## SCOPE
This is a SHADOW-internal validation exercise. It demonstrates the effect of an
isotonic draw-calibration layer applied retroactively to the settled fixture log.
It does **not** change the live prediction pipeline. `calibration_mode` defaults to
`identity` everywhere; the live engine, Elo table, GBM, Poisson model, and
`ops/result_settler.py` were not modified.

## PROTECTED FILE INTEGRITY
```
shadow_predictions.jsonl                   UNCHANGED
shadow_settlements.jsonl                   UNCHANGED
ops/result_settler.py                      UNCHANGED
src/model/wc_intelligence_engine.py        UNCHANGED
```
Replay-chain / acceptance-hash protected components: CONFIRMED UNCHANGED

## FIT SUMMARY
```
n_settled (fit + eval) : 15
Isotonic fit hash      : b7ed8237f4a178df39a77b3b6559df2d8c57a4fa56c4763dc835d65686dfb3a4
Monotonicity confirmed : True
```

**Caveat:** in-sample AFTER fits and evaluates on the same n=15 settled fixtures.
At this sample size isotonic regression can memorise labels (each predicted_D% is
near-unique, so PAVA pools almost nothing). A leave-one-out cross-validation (LOOCV)
column is included below as the credible out-of-sample read — every held-out fixture
is scored using a model refit on the other 14, so no fixture ever calibrates itself.

## BEFORE vs AFTER
```
Metric                             BEFORE  AFTER(in-samp)   AFTER(LOOCV)
------------------------------------------------------------------------
Accuracy                           53.33%          46.67%         20.00%
Predicted mean D%                  20.35%          46.67%         43.88%
Actual draw rate                   46.67%          46.67%         46.67%
Draw-rate bias                    +26.32          +0.00         +2.79
Bias classification              CRITICAL          NORMAL         NORMAL
Brier score                       0.68789         0.56957        0.70796
  (n<20 — indicative only)  
Log-loss                          1.12625         0.90302        1.22771
ECE                            STUB(n<20)      STUB(n<20)     STUB(n<20)
```

### Confusion Matrix — BEFORE (identity)
```
Predicted↓ / Actual→    HOME_WIN      DRAW  AWAY_WIN   Total
------------------------------------------------------------
HOME_WIN                       7         5         0      12
DRAW                           0         0         0       0
AWAY_WIN                       0         2         1       3
------------------------------------------------------------
Total                          7         7         1      15
```

### Confusion Matrix — AFTER, in-sample (isotonic_draw)
```
Predicted↓ / Actual→    HOME_WIN      DRAW  AWAY_WIN   Total
------------------------------------------------------------
HOME_WIN                       0         0         0       0
DRAW                           7         7         1      15
AWAY_WIN                       0         0         0       0
------------------------------------------------------------
Total                          7         7         1      15
```

### Confusion Matrix — AFTER, LOOCV out-of-sample (isotonic_draw)
```
Predicted↓ / Actual→    HOME_WIN      DRAW  AWAY_WIN   Total
------------------------------------------------------------
HOME_WIN                       0         2         0       2
DRAW                           7         3         1      11
AWAY_WIN                       0         2         0       2
------------------------------------------------------------
Total                          7         7         1      15
```

### ⚠ FINDING: SMALL-SAMPLE OVERFITTING (in-sample fit)
In-sample, the isotonic model degenerates to predicting **DRAW for all 15 fixtures** — accuracy drops from 53.33% to 46.67%, and the draw-bias 'fix' to +0.00pp is an artefact of memorising labels, not genuine
calibration. The LOOCV columns above are the honest signal: out-of-sample,
draw-rate bias is +2.79pp (NORMAL) and accuracy is 20.00%.

This is expected at n=15 with near-unique x-values and is the central reason this
work package is a **validation-only** deliverable: isotonic_draw should not be
activated for live predictions until out-of-sample performance is confirmed stable
at a larger n (see Validation Plan).

## CALIBRATION WORK PACKAGE — STATUS
```
Recommended method   : Post-hoc isotonic regression, draw dimension only
                       (implemented: src/calibration/draw_isotonic.py)
Expected impact      : Draw-rate bias toward NORMAL/WATCH; accuracy trade-off
                       uncertain until out-of-sample n is larger (see finding above)
Validation plan       : (1) LOOCV at every settler run (done, see above)
                        (2) Re-run full validator at n=20 and n=30
                        (3) Require LOOCV draw-bias <10pp AND LOOCV accuracy >= 
                            BEFORE accuracy for >=2 consecutive validator runs
                            before proposing isotonic_draw as the new default
Rollback plan         : calibration_mode flag defaults to "identity"; no data
                        files are written by the calibration layer; unset/
                        misconfigured DRAW_CALIBRATION_MODE falls back to identity
```

## SUCCESS CRITERIA  *(as specified in the R-1 work package)*
```
Draw bias reduced below 10pp        : PASS  (in-sample=+0.00pp, LOOCV=+2.79pp)
No replay-chain violations          : PASS
Deterministic outputs preserved     : PASS
Probability sums remain valid (100%): PASS  (isotonic_draw output violations=0)
Rollback path confirmed             : PASS
```
*Note: 4 settled fixture(s) have stored H+D+A summing to 99.9%/100.1% due to pre-existing 1-decimal rounding in the upstream*
*prediction log (not introduced by this layer). Identity mode faithfully reproduces those*
*values byte-for-byte, as required for replay compatibility; isotonic_draw mode*
*renormalises and always sums to exactly 100%.*

## VERDICT
**All four literal success criteria pass.** The calibration layer is correctly
implemented, deterministic, replay-safe, conservation-preserving, and fully
rollback-capable. However, the in-sample bias improvement (+26.32pp → +0.00pp) is inflated by
small-sample overfitting (see finding above). The LOOCV out-of-sample bias is +2.79pp (NORMAL).

**Recommendation:** keep `calibration_mode` at the `identity` default. The layer is
built, tested, and proven mechanically sound — but the overfitting finding means
activating `isotonic_draw` for live predictions now would trade a known, measured
bias for an unmeasured small-sample variance risk. Re-run this validator as n grows
(next natural checkpoint: n=20, when Brier/ECE also unlock) before reconsidering.

**Roadmap state: remains SHADOW_HARDENING.** `calibration_mode` default is
unchanged (`identity`). This report does not advance the system to PAPER and
does not modify any live prediction path.

---
*Generated by R-1 Draw Calibration Work Package validator · 2026-06-16T06:44:35.731383+00:00*