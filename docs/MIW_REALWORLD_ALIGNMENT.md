# MIW Phase 10 — Real-World Alignment Layer

> **Scope.** A corrective layer that closes the gap between theoretical model performance and **real-world betting execution** performance. Previous phases are **not redesigned**; this layer adds *financial realism* on top of them. **No code.** Focus: CLV as a real edge signal, backtests that reflect real constraints, modeled slippage/liquidity, market-efficiency differences anchored to the sharp market.

---

## 1. Executive Summary

The system's math and model are solid; the real risk is in the **translation between model and world**. Phase 10 drops four anchors:

1. **Sharp Anchor CLV** — CLV is now measured against the **sharp market (Pinnacle/Betfair) devigged fair value**, not soft-book closing -> circularity ends, feedback stops rewarding a statistical artifact.
2. **Execution Model** — fill probability, slippage, latency, liquidity -> the gap between "obtainable odds" and "quoted odds" is modeled.
3. **Anti-Leakage Audit** — future-information detection, time-separated calibration/threshold, non-self-referential CLV.
4. **Realistic Backtest** — "theoretical ROI" vs "realistic ROI" + realism-tax decomposition.

**Final Real-World Risk Score: 60/100 (current) -> target ~28/100 (after Phase 10).** Scale: 0 = fully aligned/no hidden risk, 100 = maximal hidden real-world risk.

---

## 2. Sharp Anchor CLV Model

### 2.1 Devig (fair probability extraction)
Sharp book raw odds $o_i$, implied $q_i = 1/o_i$, overround $\sum_j q_j > 1$.
- **Proportional:** $p_i = q_i / \sum_j q_j$ (simple, small bias).
- **Shin (recommended):** solve for insider parameter $z$; corrects favorite-longshot bias, most realistic fair probability for sharp books.
$p^{sharp}_i$ = post-devig sharp fair probability; $o^{sharp,fair}_i = 1/p^{sharp}_i$.

### 2.2 Corrected CLV definition
Traditional CLV compares the odds you got to **your own book's** closing:
$$ \text{CLV}^{trad}_i = \frac{o^{bet}_i}{o^{close,soft}_i} - 1 $$
Sharp-anchored CLV compares to the true-value reference:
$$ \text{CLV}^{sharp}_i = \frac{o^{exec}_i}{o^{sharp,fair}_i} - 1 = o^{exec}_i \cdot p^{sharp}_i - 1 $$
Positive -> you got an above-fair price relative to sharp consensus (real edge).

### 2.3 Why traditional CLV is biased
- Soft closing includes **that book's bias + your own action** (if you are the steam) -> not a clean fair value.
- In high-vig markets soft closing is artificially close -> CLV+ but ROI-.
- In thin markets soft closing is noisy.

### 2.4 How it prevents feedback collapse
The reference is now neither the book you bet into nor your own model; it is an **independent sharp consensus**. The SHAP-CLV loop stops being self-referential; the reward signal is denoised and drift decreases.

---

## 3. Execution Simulation Layer

| Component | Model |
|---|---|
| Pre-placement odds drift | Movement between signal $t_s$ -> placement $t_p$; expected drift $\delta$ from steam direction + time-to-kickoff |
| Fill probability curve | $P_{fill}(o_{req},\tau)=\sigma(a - b\frac{o_{req}-o_{mkt}}{\theta} - c\tau - d/\ell)$ ($\ell$ liquidity) |
| Liquidity constraint | Matchable volume per market $L_{mkt}$; partial fill if stake > available |
| Latency impact | Delay $d$ -> odds at $t_s+d$; drift applied over $d$ |
| Stake slippage | $o^{exec}=o^{req}-\text{slip}(s,\ell)$; large stake + thin liquidity -> larger slip |

Expected executed odds:
$$ \mathbb{E}[o^{exec}_i] = (o^{quote}_i - \delta_i)(1 - \text{slip}(s_i,\ell_i)) $$
Execution-adjusted ROI:
$$ \text{ROI}_{exec} = \frac{\sum_i P_{fill,i}\,s^{matched}_i\,(o^{exec}_i\,\mathbb{1}_{win,i} - 1)}{\sum_i P_{fill,i}\,s^{matched}_i} $$

---

## 4. Anti-Leakage Reality Audit Engine

### 4.1 Leakage scoring function
Per feature $f$:
$$ L_f = w_1\,\mathbb{1}[t_f > t_{decision}] + w_2\,\mathbb{1}[\text{closing-derived}] + w_3\,\text{MI-anomaly}_f + w_4\,\text{CV-gap}_f $$
Features exceeding the $L_f$ threshold are rejected/quarantined.

### 4.2 Dataset integrity checks
- Timestamp monotonicity; train / calib / threshold / test time-ordered and **disjoint**.
- No row in multiple folds; **embargo/purge** gap between train and test.
- CLV label must **not** appear in the feature matrix.

### 4.3 "Future information detection rules"
1. Feature timestamped after kickoff -> **reject**.
2. Closing/late-snapshot-derived feature for pre-match prediction -> **flag**.
3. Suspiciously high mutual information with target -> target-leakage test.
4. Walk-forward only; random CV forbidden.
5. League Residual Layer fit only on the past window.

---

## 5. CLV Feedback Correction Model

### 5.1 Bookmaker bias separation
Regress soft closing on sharp fair; the systematic component = bias, the residual = true edge:
$$ p^{soft,close}_i = p^{sharp}_i + \beta_{bk} + \epsilon_i \;\Rightarrow\; \text{bias}_{bk} = \hat\beta_{bk} $$

### 5.2 Corrected CLV
$$ \text{CLV}^{corr}_i = \underbrace{(p^{sharp}_i - p^{model}_i)}_{\text{edge vs sharp}}\cdot \underbrace{P_{fill,i}}_{\text{execution realism}}\cdot \underbrace{\omega(\ell_i)}_{\text{liquidity weight}} - \text{bias}_{bk} $$
$\omega(\ell)$ downweights low-liquidity matches ($\omega\to 0$ in thin markets). Feedback (threshold / calibration / trust / regime) now uses the $\text{CLV}^{corr}$ reward — execution-realistic, sharp-anchored, bias-stripped.

---

## 6. Market Efficiency Regime System

Efficiency score:
$$ \text{Eff}_{lg} = g(\text{margin}_{lg},\ \text{liquidity}_{lg},\ \text{closing-predictiveness}_{lg},\ \text{volume}_{lg}) $$

| Regime | Example | CLV interpretation | Confidence scaling |
|---|---|---|---|
| Efficient | EPL, Top-5 Europe | Beating the sharp is rare & very meaningful (strong edge) | High trust in sharp anchor ($\lambda$ up); take CLV+ seriously |
| Semi-efficient | Mid leagues | Mixed; bias stripping critical | Medium $\lambda$; moderate shrinkage |
| Inefficient | Summer leagues, low liquidity | CLV noisy, may be an artifact -> discount | Sharp anchor weak ($\lambda$ down); heavy uncertainty shrinkage; trust model but small stake |

Regime is **soft/probabilistic** (instead of a hard threshold), consistent with the audit recommendation.

---

## 7. Realistic Backtest Engine

The backtest must include: slippage, pre-placement drift, **missed bets** due to line movement ($P_{fill}<1$), liquidity filtering (skip below min liquidity), execution probability.

$$ \text{ROI}_{theo} = \frac{\sum_i s_i\,(o^{quote}_i\,\mathbb{1}_{win,i}-1)}{\sum_i s_i} $$
$$ \text{ROI}_{real} = \frac{\sum_i \mathbb{1}_{placed,i}\,P_{fill,i}\,s^{matched}_i\,(o^{exec}_i\,\mathbb{1}_{win,i}-1)}{\sum_i \mathbb{1}_{placed,i}\,P_{fill,i}\,s^{matched}_i} $$

**Realism tax** $= \text{ROI}_{theo} - \text{ROI}_{real}$, decomposed into components: slippage / fill loss / liquidity filter / drift. The backtest reports both ROIs; decisions are made only on $\text{ROI}_{real}$.

---

## 8. Failure Mode Analysis

| Question | Finding |
|---|---|
| Where performance is overestimated | Theoretical ROI on quoted/best-quote odds; assuming unobtainable prices; no slippage |
| Where CLV becomes misleading | Vs soft closing (inflates if you are the steam); high-vig & thin markets; artifact in inefficient regime |
| Where portfolio risk becomes invisible | Correlation spikes in regime shift; **fill correlation** (in fast markets bets fail to fill together); liquidity-correlated simultaneous drawdown |
| Where calibration breaks under real execution | Calibrated to quote-implied, but executed odds differ -> EV wrong; efficient-regime calibration applied to inefficient |

---

## 9. Implementation Roadmap

| Step | Deliverable | Dependency | Risk |
|---|---|---|---|
| 10.1 | Sharp provider adapter (Pinnacle/Betfair proxy) + Shin devig | Phase 9 PAL | Medium |
| 10.2 | Sharp-anchored CLV definition + new fields in clv_history | 10.1 | Low |
| 10.3 | Execution model (fill curve, slippage, latency, liquidity) | Phase 9 snapshot history | High |
| 10.4 | Anti-leakage audit engine (leakage score + integrity + future-info rules) | - | Medium |
| 10.5 | CLV feedback correction (bias separation + $\text{CLV}^{corr}$) -> wire into Phase 7 loop | 10.2,10.3 | Medium |
| 10.6 | Market efficiency regime (soft) + per-regime CLV/confidence scaling | Phase 6 efficiency | Medium |
| 10.7 | Realistic backtest engine (ROI_theo vs ROI_real + realism tax) | 10.3 | High |
| 10.8 | Failure-mode monitoring dashboard (CLV-ROI divergence, fill correlation, segment-ECE) | 10.1-10.7 | Low |

**Estimated duration:** ~8-10 weeks. Critical path: 10.1 -> 10.2 -> 10.5 and 10.3 -> 10.7.

---

## 10. Final System Risk Score

| Risk dimension | Current (0=safe,100=risky) | Post-Phase-10 target |
|---|---|---|
| CLV artifact risk | 72 | 26 |
| Execution/sim-live gap | 70 | 30 |
| Leakage risk | 58 | 22 |
| Regime misinterpretation risk | 52 | 30 |
| Invisible portfolio risk | 50 | 32 |
| **Weighted Real-World Risk Score** | **60 / 100** | **~28 / 100** |

> **Summary:** Phase 10 targets not model accuracy but **financial reality**. Sharp-anchor CLV + execution model + anti-leakage audit + realistic backtest together systematically eliminate the "profitable on paper, losing live" scenario. No previous phase changes; this is only a reality filter on top.
