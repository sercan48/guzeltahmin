# R1_PHASE2_EVIDENCE_REPORT
**R-1 Draw Calibration — Phase 2: Evidence Collection Only**  
**Generated:** 2026-06-16T11:59:45.138639+00:00  
**Roadmap phase:** SHADOW_HARDENING  
**n_settled:** 16  (formal checkpoints: n>=20, n>=30; reached: NONE YET)  

This report is evidence collection only. No code changes were made to the Poisson engine, Elo model, GBM model, confidence formula, settlement pipeline, replay chain, or any acceptance-hash protected component. `calibration_mode` remains `identity` in the live pipeline.

---

## 1. METRICS BY MODE
```
Metric                           IDENTITY  ISOTONIC(in-s)  ISOTONIC(LOOCV)
--------------------------------------------------------------------------
Accuracy                           50.00%          50.00%           43.75%
Draw-rate bias (pp)               +29.55           +0.00            +2.63
Bias classification              CRITICAL          NORMAL           NORMAL
Brier score                       0.70504         0.56264          0.70079
ECE                            STUB(n<20)      STUB(n<20)       STUB(n<20)
```
*Brier/ECE are gated at n>=20 by convention; shown above regardless and labelled `STUB(n<20)` while n=16.*

## 2. DRAW-CLASS PRECISION / RECALL / F1
```
                     TP   FP   FN   Precision    Recall        F1
IDENTITY              0    0    8         n/a     0.00%       n/a
ISOTONIC (in-samp)    8    8    0      50.00%   100.00%    66.67%
ISOTONIC (LOOCV)      7    8    1      46.67%    87.50%    60.87%
```
Identity mode structurally never predicts DRAW (the Poisson-derived draw probability is capped below the modal home/away probability in every settled fixture so far), so its draw recall/precision are 0%/undefined by construction — not a defect, just the bias this work package exists to evidence.

## 3. CONFIDENCE INTERVALS (95%, Wilson score)
```
Accuracy — identity            : 50.00%  CI [28.00, 72.00]
Accuracy — isotonic (in-samp)  : 50.00%  CI [28.00, 72.00]
Accuracy — isotonic (LOOCV)    : 43.75%  CI [23.10, 66.82]
Draw rate — actual             : 50.00%  CI [28.00, 72.00]
Draw bias — identity           : +29.55pp  approx-CI [5.05, 54.05]
Draw bias — isotonic (in-samp) : +0.00pp  approx-CI [-24.50, 24.50]
Draw bias — isotonic (LOOCV)   : +2.63pp  approx-CI [-21.87, 27.13]
```
All intervals are wide at n=16 — expected, and the reason the task gates the formal verdict on reaching n>=20 and n>=30 rather than reading these point estimates directly.

## 4. DIAGNOSIS — CAUSE OF THE LOOCV DEGRADATION
In-sample accuracy (50.00%) vs LOOCV accuracy (43.75%): a 6.25pp gap. Four candidate causes were checked against the data:

**Overfitting — CONFIRMED**  
In-sample fit uses 14 distinct predicted-D% values across 16 fixtures (near 1:1), so PAVA pools almost nothing and the isotonic step function can memorise per-fixture labels. In-sample, the model degenerates to predicting DRAW for all 16/16 fixtures — its in-sample accuracy (50.00%) matching identity's (50.00%) is coincidental (it equals the raw actual draw rate), not evidence of a good fit. LOOCV accuracy is 43.75% (identity=50.00%).

**Sample instability — CONFIRMED**  
Each LOOCV fold refits on only 15 points; with 16 total fixtures, removing any single point can materially shift the fitted step function (high-leverage points). 8 of 8 previously-correct decisive (non-draw) calls flipped to incorrect under LOOCV — a high flip rate relative to fold count indicates the fit is not stable under leave-one-out perturbation.

**Class imbalance — CONFIRMED**  
Actual outcome counts in the settled sample: HOME_WIN=7, DRAW=8, AWAY_WIN=1 (n=16). AWAY_WIN is severely under-represented, so any fold that removes the sole/rare AWAY_WIN example(s) leaves the isotonic fit with no signal to keep the draw curve from dominating that region of predicted-D%.

**Draw overshoot — CONFIRMED**  
8 of 8 flips from correct-under-identity to incorrect-under-LOOCV are flips specifically *to* a DRAW prediction that was wrong — i.e. the calibrated D% overtakes H%/A% for fixtures that were not actually draws.

**4 of 4 candidate causes confirmed.** These are not mutually exclusive: at this sample size, class imbalance (too few AWAY_WIN examples) is the structural root cause, which manifests as sample instability under LOOCV (removing a rare-class point swings the fit) and is expressed in the result pattern as overfitting in-sample / draw overshoot out-of-sample.

### Fixtures that flipped from correct (identity) to incorrect (LOOCV isotonic_draw)
```
2026-06-11  Mexico vs South Africa: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-12  South Korea vs Czechia: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-13  United States vs Paraguay: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-14  Haiti vs Scotland: actual=AWAY_WIN, identity_pred=AWAY_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-14  Australia vs Turkey: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-14  Germany vs Curaçao: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.33
2026-06-14  Ivory Coast vs Ecuador: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
2026-06-15  Sweden vs Tunisia: actual=HOME_WIN, identity_pred=HOME_WIN (correct) -> loocv_pred=DRAW (incorrect), loocv D%=53.85
```

## 5. APPROVAL REQUIREMENTS (checked against LOOCV / out-of-sample read)
```
Draw bias < 10pp                    : PASS  (LOOCV=+2.63pp)
Brier Score improves                : PASS  (identity=0.70504, LOOCV=0.70079)
ECE improves                        : FAIL  (n<20 — ECE not yet computable)
Accuracy not materially degraded    : FAIL  (identity=50.00%, LOOCV=43.75%, threshold=-5pp)
```
*All four requirements must hold for APPROVE_CALIBRATION. This section is informational*
*below n=20 — see verdict below.*

## 6. VERDICT (legacy 4-requirement gate, retained for audit continuity)
### **NEED_MORE_DATA**

n_settled=16 has not yet reached the first formal checkpoint (n>=20). Per task instructions, no APPROVE/REJECT determination is made below n=20.

## 7. FINAL CLASSIFICATION
Per current task spec: recommend calibration only if it improves BOTH out-of-sample Brier Score AND out-of-sample ECE, AND does not collapse class diversity.
```
Out-of-sample (LOOCV) Brier improves : YES  (identity=0.70504, LOOCV=0.70079)
Out-of-sample (LOOCV) ECE improves   : NO  (n<20 — ECE not yet computable)
Class diversity preserved (in-samp)  : NO — COLLAPSED  (1 distinct predicted classes, top class DRAW=100.0%)
Class diversity preserved (LOOCV)    : NO — COLLAPSED  (2 distinct predicted classes, top class DRAW=93.8%)
```
### **NEED_MORE_DATA**

n_settled=16 has not yet reached the first formal checkpoint (n>=20).

**Action:** `calibration_mode` remains `"identity"` in the live pipeline (no change). System remains in **SHADOW_HARDENING**. No PAPER-phase transition. No isotonic regression, probability generation, confidence scoring, or Poisson/Elo/GBM changes were made.

## 8. PROTECTED-FILE INTEGRITY (read-only confirmation)
```
shadow_predictions.jsonl                   758a2b783b425117c96070aba4bf3ca52c1b1ac54e0f053aa0a9ea1073b4966c
shadow_settlements.jsonl                   526124b1da01ed4e4b1aa0cf0eacfa9c7f45d9b012ff48fc40794113efdda54f
ops/result_settler.py                      7819b8cafde155fc5f1da88171847b6d7c355e4eeab613c081ed03825a434e87
src/model/wc_intelligence_engine.py        ad1d531d4457f9712a5ff79e230cff5888465a529e53766e0266a91e9be8a56b
```

---
*Generated by R-1 Phase 2 evidence collector · 2026-06-16T11:59:45.138639+00:00*