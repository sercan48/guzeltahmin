# R1_PHASE2_EVIDENCE_REPORT
**R-1 Draw Calibration — Phase 2: Evidence Collection Only**  
**Generated:** 2026-06-17T11:53:36.566459+00:00  
**Roadmap phase:** SHADOW_HARDENING  
**n_settled:** 20  (formal checkpoints: n>=20, n>=30; reached: 20)  

This report is evidence collection only. No code changes were made to the Poisson engine, Elo model, GBM model, confidence formula, settlement pipeline, replay chain, or any acceptance-hash protected component. `calibration_mode` remains `identity` in the live pipeline.

---

## 1. METRICS BY MODE
```
Metric                           IDENTITY  ISOTONIC(in-s)  ISOTONIC(LOOCV)
--------------------------------------------------------------------------
Accuracy                           60.00%          35.00%           10.00%
Draw-rate bias (pp)               +19.70           +0.00            +0.79
Bias classification              CRITICAL          NORMAL           NORMAL
Brier score                       0.61379         0.55423          0.66021
ECE                               0.28845         0.03735          0.27105
```
*Brier/ECE are gated at n>=20 by convention; shown above regardless and labelled `STUB(n<20)` while n=20.*

## 2. DRAW-CLASS PRECISION / RECALL / F1
```
                     TP   FP   FN   Precision    Recall        F1
IDENTITY              0    0    8         n/a     0.00%       n/a
ISOTONIC (in-samp)    0    5    8       0.00%     0.00%       n/a
ISOTONIC (LOOCV)      0   10    8       0.00%     0.00%       n/a
```
Identity mode structurally never predicts DRAW (the Poisson-derived draw probability is capped below the modal home/away probability in every settled fixture so far), so its draw recall/precision are 0%/undefined by construction — not a defect, just the bias this work package exists to evidence.

## 3. CONFIDENCE INTERVALS (95%, Wilson score)
```
Accuracy — identity            : 60.00%  CI [38.66, 78.12]
Accuracy — isotonic (in-samp)  : 35.00%  CI [18.12, 56.71]
Accuracy — isotonic (LOOCV)    : 10.00%  CI [2.79, 30.10]
Draw rate — actual             : 40.00%  CI [21.88, 61.34]
Draw bias — identity           : +19.70pp  approx-CI [-1.77, 41.18]
Draw bias — isotonic (in-samp) : +0.00pp  approx-CI [-21.47, 21.47]
Draw bias — isotonic (LOOCV)   : +0.79pp  approx-CI [-20.68, 22.26]
```
All intervals are wide at n=20 — expected, and the reason the task gates the formal verdict on reaching n>=20 and n>=30 rather than reading these point estimates directly.

## 4. DIAGNOSIS — CAUSE OF THE LOOCV DEGRADATION
In-sample accuracy (35.00%) vs LOOCV accuracy (10.00%): a 25.00pp gap. Four candidate causes were checked against the data:

**Overfitting — CONFIRMED**  
In-sample fit uses 17 distinct predicted-D% values across 20 fixtures (near 1:1), so PAVA pools almost nothing and the isotonic step function can memorise per-fixture labels. In-sample accuracy (35.00%) vs LOOCV accuracy (10.00%) gap = 25.00pp. LOOCV accuracy is 10.00% (identity=60.00%).

**Sample instability — CONFIRMED**  
Each LOOCV fold refits on only 19 points; with 20 total fixtures, removing any single point can materially shift the fitted step function (high-leverage points). 10 of 12 previously-correct decisive (non-draw) calls flipped to incorrect under LOOCV — a high flip rate relative to fold count indicates the fit is not stable under leave-one-out perturbation.

**Class imbalance — CONFIRMED**  
Actual outcome counts in the settled sample: HOME_WIN=10, DRAW=8, AWAY_WIN=2 (n=20). AWAY_WIN is severely under-represented, so any fold that removes the sole/rare AWAY_WIN example(s) leaves the isotonic fit with no signal to keep the draw curve from dominating that region of predicted-D%.

**Draw overshoot — CONFIRMED**  
10 of 10 flips from correct-under-identity to incorrect-under-LOOCV are flips specifically *to* a DRAW prediction that was wrong — i.e. the calibrated D% overtakes H%/A% for fixtures that were not actually draws.

**4 of 4 candidate causes confirmed.** These are not mutually exclusive: at this sample size, class imbalance (too few AWAY_WIN examples) is the structural root cause, which manifests as sample instability under LOOCV (removing a rare-class point swings the fit) and is expressed in the result pattern as overfitting in-sample / draw overshoot out-of-sample.

### Fixtures that flipped from correct (identity) to incorrect (LOOCV isotonic_draw)
```
2026-06-11  Mexico vs South Africa: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-12  South Korea vs Czechia: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-13  United States vs Paraguay: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-14  Haiti vs Scotland: actual=AWAY_WIN, identity_pred=AWAY_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-14  Australia vs Turkey: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-14  Ivory Coast vs Ecuador: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-15  Sweden vs Tunisia: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-16  France vs Senegal: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-16  Iraq vs Norway: actual=AWAY_WIN, identity_pred=AWAY_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
2026-06-17  Austria vs Jordan: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=43.75
```

## 5. APPROVAL REQUIREMENTS (checked against LOOCV / out-of-sample read)
```
Draw bias < 10pp                    : PASS  (LOOCV=+0.79pp)
Brier Score improves                : FAIL  (identity=0.61379, LOOCV=0.66021)
ECE improves                        : PASS  (identity=0.28845, LOOCV=0.27105)
Accuracy not materially degraded    : FAIL  (identity=60.00%, LOOCV=10.00%, threshold=-5pp)
```
*All four requirements must hold for APPROVE_CALIBRATION. This section is informational*
*below n=20 — see verdict below.*

## 6. VERDICT (legacy 4-requirement gate, retained for audit continuity)
### **REJECT_CALIBRATION**

At checkpoint n>=20, the following requirement(s) failed: brier_improves, accuracy_not_materially_degraded. calibration_mode remains "identity"; system remains in SHADOW_HARDENING.

## 7. CLASS DISTRIBUTION (identity vs isotonic, LOOCV out-of-sample)
```
Outcome         Identity  Isotonic(in-s)  Isotonic(LOOCV)
HOME_WIN           80.0%           60.0%            40.0%
DRAW                0.0%           25.0%            50.0%
AWAY_WIN           20.0%           15.0%            10.0%
distinct               2               3                3
```
Diversity ratio (LOOCV distinct / identity distinct): **1.50**  
DRAW share of LOOCV predictions: **50.0%**  
Draw-rate bias — identity: +19.70pp (CRITICAL)  vs isotonic LOOCV: +0.79pp (NORMAL)

## 8. CALIBRATION CURVES (reliability bins, gated n>=20)
```
Conf band   n(id)   acc(id)  n(loocv)  acc(loocv)
30-40           3    100.0%        17       11.8%
40-50           2    100.0%         2        0.0%
50-60           6     33.3%         0         n/a
60-70           5     60.0%         0         n/a
70-80           2     50.0%         0         n/a
80-93           2     50.0%         1        0.0%
```
Perfect calibration would show acc(band) == mean-confidence-of-band; large gaps indicate over/under-confidence within that band.

## 9. AUTOMATIC REJECTION CHECK
Per checkpoint protocol, isotonic is auto-rejected if ANY of:
```
diversity_ratio < 0.50                : clear  (ratio=1.50)
DRAW share (LOOCV) > 70%               : clear  (50.0%)
ECE does not improve (out-of-sample)   : clear  (identity=0.28845, LOOCV=0.27105)
Brier does not improve (out-of-sample) : TRIGGERED  (identity=0.61379, LOOCV=0.66021)
```
Triggered reasons:
- out-of-sample Brier did not improve (0.61379 -> 0.66021)

## 10. FINAL CLASSIFICATION
### **CALIBRATION_NOT_REQUIRED**

At checkpoint n>=20, auto-rejected: out-of-sample Brier did not improve (0.61379 -> 0.66021). calibration_mode remains "identity"; system remains in SHADOW_HARDENING.

**Action:** `calibration_mode` remains `"identity"` in the live pipeline (no change). System remains in **SHADOW_HARDENING**. No PAPER-phase transition. No isotonic regression, probability generation, confidence scoring, or Poisson/Elo/GBM changes were made.

## 11. PROTECTED-FILE INTEGRITY (read-only confirmation)
```
shadow_predictions.jsonl                   758a2b783b425117c96070aba4bf3ca52c1b1ac54e0f053aa0a9ea1073b4966c
shadow_settlements.jsonl                   965841a7264822399c1439a5904314e0cee6055cfa7a860b58c98da5bdfd1b06
ops/result_settler.py                      7819b8cafde155fc5f1da88171847b6d7c355e4eeab613c081ed03825a434e87
src/model/wc_intelligence_engine.py        ad1d531d4457f9712a5ff79e230cff5888465a529e53766e0266a91e9be8a56b
```

---
*Generated by R-1 Phase 2 evidence collector · 2026-06-17T11:53:36.566459+00:00*