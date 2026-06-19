# R1_PHASE2_EVIDENCE_REPORT
**R-1 Draw Calibration — Phase 2: Evidence Collection Only**  
**Generated:** 2026-06-18T11:51:35.647021+00:00  
**Roadmap phase:** SHADOW_HARDENING  
**n_settled:** 24  (formal checkpoints: n>=20, n>=30; reached: 20)  

This report is evidence collection only. No code changes were made to the Poisson engine, Elo model, GBM model, confidence formula, settlement pipeline, replay chain, or any acceptance-hash protected component. `calibration_mode` remains `identity` in the live pipeline.

---

## 1. METRICS BY MODE
```
Metric                           IDENTITY  ISOTONIC(in-s)  ISOTONIC(LOOCV)
--------------------------------------------------------------------------
Accuracy                           62.50%          50.00%           50.00%
Draw-rate bias (pp)               +17.35           +0.00            +0.81
Bias classification              CRITICAL          NORMAL           NORMAL
Brier score                       0.60022         0.55106          0.60675
ECE                               0.30796         0.14258          0.24346
```
*Brier/ECE are gated at n>=20 by convention; shown above regardless and labelled `STUB(n<20)` while n=24.*

## 2. DRAW-CLASS PRECISION / RECALL / F1
```
                     TP   FP   FN   Precision    Recall        F1
IDENTITY              0    0    9         n/a     0.00%       n/a
ISOTONIC (in-samp)    0    3    9       0.00%     0.00%       n/a
ISOTONIC (LOOCV)      0    3    9       0.00%     0.00%       n/a
```
Identity mode structurally never predicts DRAW (the Poisson-derived draw probability is capped below the modal home/away probability in every settled fixture so far), so its draw recall/precision are 0%/undefined by construction — not a defect, just the bias this work package exists to evidence.

## 3. CONFIDENCE INTERVALS (95%, Wilson score)
```
Accuracy — identity            : 62.50%  CI [42.71, 78.84]
Accuracy — isotonic (in-samp)  : 50.00%  CI [31.43, 68.57]
Accuracy — isotonic (LOOCV)    : 50.00%  CI [31.43, 68.57]
Draw rate — actual             : 37.50%  CI [21.16, 57.29]
Draw bias — identity           : +17.35pp  approx-CI [-2.02, 36.71]
Draw bias — isotonic (in-samp) : +0.00pp  approx-CI [-19.37, 19.37]
Draw bias — isotonic (LOOCV)   : +0.81pp  approx-CI [-18.55, 20.18]
```
All intervals are wide at n=24 — expected, and the reason the task gates the formal verdict on reaching n>=20 and n>=30 rather than reading these point estimates directly.

## 4. DIAGNOSIS — CAUSE OF THE LOOCV DEGRADATION
In-sample accuracy (50.00%) vs LOOCV accuracy (50.00%): a 0.00pp gap. Four candidate causes were checked against the data:

**Overfitting — not confirmed**  
In-sample fit uses 21 distinct predicted-D% values across 24 fixtures (near 1:1), so PAVA pools almost nothing and the isotonic step function can memorise per-fixture labels. In-sample accuracy (50.00%) vs LOOCV accuracy (50.00%) gap = 0.00pp. LOOCV accuracy is 50.00% (identity=62.50%).

**Sample instability — not confirmed**  
Each LOOCV fold refits on only 23 points; with 24 total fixtures, removing any single point can materially shift the fitted step function (high-leverage points). 3 of 15 previously-correct decisive (non-draw) calls flipped to incorrect under LOOCV — a high flip rate relative to fold count indicates the fit is not stable under leave-one-out perturbation.

**Class imbalance — not confirmed**  
Actual outcome counts in the settled sample: HOME_WIN=12, DRAW=9, AWAY_WIN=3 (n=24). AWAY_WIN is severely under-represented, so any fold that removes the sole/rare AWAY_WIN example(s) leaves the isotonic fit with no signal to keep the draw curve from dominating that region of predicted-D%.

**Draw overshoot — CONFIRMED**  
3 of 3 flips from correct-under-identity to incorrect-under-LOOCV are flips specifically *to* a DRAW prediction that was wrong — i.e. the calibrated D% overtakes H%/A% for fixtures that were not actually draws.

**1 of 4 candidate causes confirmed.** These are not mutually exclusive: at this sample size, class imbalance (too few AWAY_WIN examples) is the structural root cause, which manifests as sample instability under LOOCV (removing a rare-class point swings the fit) and is expressed in the result pattern as overfitting in-sample / draw overshoot out-of-sample.

### Fixtures that flipped from correct (identity) to incorrect (LOOCV isotonic_draw)
```
2026-06-14  Australia vs Turkey: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=39.13
2026-06-14  Ivory Coast vs Ecuador: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=39.13
2026-06-15  Sweden vs Tunisia: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=39.13
```

## 5. APPROVAL REQUIREMENTS (checked against LOOCV / out-of-sample read)
```
Draw bias < 10pp                    : PASS  (LOOCV=+0.81pp)
Brier Score improves                : FAIL  (identity=0.60022, LOOCV=0.60675)
ECE improves                        : PASS  (identity=0.30796, LOOCV=0.24346)
Accuracy not materially degraded    : FAIL  (identity=62.50%, LOOCV=50.00%, threshold=-5pp)
```
*All four requirements must hold for APPROVE_CALIBRATION. This section is informational*
*below n=20 — see verdict below.*

## 6. VERDICT (legacy 4-requirement gate, retained for audit continuity)
### **REJECT_CALIBRATION**

At checkpoint n>=20, the following requirement(s) failed: brier_improves, accuracy_not_materially_degraded. calibration_mode remains "identity"; system remains in SHADOW_HARDENING.

## 7. CLASS DISTRIBUTION (identity vs isotonic, LOOCV out-of-sample)
```
Outcome         Identity  Isotonic(in-s)  Isotonic(LOOCV)
HOME_WIN           79.2%           66.7%            66.7%
DRAW                0.0%           12.5%            12.5%
AWAY_WIN           20.8%           20.8%            20.8%
distinct               2               3                3
```
Diversity ratio (LOOCV distinct / identity distinct): **1.50**  
DRAW share of LOOCV predictions: **12.5%**  
Draw-rate bias — identity: +17.35pp (CRITICAL)  vs isotonic LOOCV: +0.81pp (NORMAL)

## 8. CALIBRATION CURVES (reliability bins, gated n>=20)
```
Conf band   n(id)   acc(id)  n(loocv)  acc(loocv)
30-40           3    100.0%        17       58.8%
40-50           4    100.0%         5       40.0%
50-60           6     33.3%         0         n/a
60-70           5     60.0%         2        0.0%
70-80           3     66.7%         0         n/a
80-93           3     33.3%         0         n/a
```
Perfect calibration would show acc(band) == mean-confidence-of-band; large gaps indicate over/under-confidence within that band.

## 9. AUTOMATIC REJECTION CHECK
Per checkpoint protocol, isotonic is auto-rejected if ANY of:
```
diversity_ratio < 0.50                : clear  (ratio=1.50)
DRAW share (LOOCV) > 70%               : clear  (12.5%)
ECE does not improve (out-of-sample)   : clear  (identity=0.30796, LOOCV=0.24346)
Brier does not improve (out-of-sample) : TRIGGERED  (identity=0.60022, LOOCV=0.60675)
```
Triggered reasons:
- out-of-sample Brier did not improve (0.60022 -> 0.60675)

## 10. FINAL CLASSIFICATION
### **CALIBRATION_NOT_REQUIRED**

At checkpoint n>=20, auto-rejected: out-of-sample Brier did not improve (0.60022 -> 0.60675). calibration_mode remains "identity"; system remains in SHADOW_HARDENING.

**Action:** `calibration_mode` remains `"identity"` in the live pipeline (no change). System remains in **SHADOW_HARDENING**. No PAPER-phase transition. No isotonic regression, probability generation, confidence scoring, or Poisson/Elo/GBM changes were made.

## 11. PROTECTED-FILE INTEGRITY (read-only confirmation)
```
shadow_predictions.jsonl                   758a2b783b425117c96070aba4bf3ca52c1b1ac54e0f053aa0a9ea1073b4966c
shadow_settlements.jsonl                   396dcb8a32d0332bb3741bd67c6b848e2f00f10e08833190056db2ccc6c8716c
ops/result_settler.py                      7819b8cafde155fc5f1da88171847b6d7c355e4eeab613c081ed03825a434e87
src/model/wc_intelligence_engine.py        ad1d531d4457f9712a5ff79e230cff5888465a529e53766e0266a91e9be8a56b
```

---
*Generated by R-1 Phase 2 evidence collector · 2026-06-18T11:51:35.647021+00:00*