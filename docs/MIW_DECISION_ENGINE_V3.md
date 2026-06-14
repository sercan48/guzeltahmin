# MIW Phase 7 — Market-Aware Calibration & Decision Engine

> **Scope.** Phase 7 embeds the market intelligence produced in Phase 6 (efficiency, trust, sharp confidence, expected CLV, regime) directly into the prediction stack. This document is **design only — no code**. It is fully compatible with the existing system: the ECE-minimizer calibrator, Decision Score v2 triple-gate, the Optuna utility, the League Residual Layer, and the safety-rollback are all preserved; Phase 7 is layered *on top*.

---

## 1. Architecture

```
Raw Model (Model Stack)  --> p_raw
        |
        v
[A] Market-Aware Calibration Layer  <-- E_lg, E_bk, T_mkt, S_sharp, e_clv, regime
        |  p_final (logit blend)
        v
[B] Confidence Engine  --> C_pred, C_mkt, C_combined
        |
        v
[C] Decision Score v3  <-- EV, e_clv, Edge, T_mkt, S_sharp, M_regime
        |  DS3
        v
[D] Dynamic Threshold Engine v2  <-- league / regime / market offsets
        |  accept/reject (quad-gate)
        v
[E] Risk Engine  --> V (volatility), D (drawdown), Lambda (confidence decay)
        |
        v
[F] Portfolio Layer  --> stake (fractional Kelly + exposure caps)
        |
        v
   Bet / Pass
        |  (result + closing)
        v
[G] Feedback Integration  --> update thresholds / calibration / trust / regime
```

| Component | Inputs | Output | Link to existing system |
|---|---|---|---|
| A. Market-Aware Calibration | p_raw, E_lg, E_bk, T_mkt, S, e_clv, regime | p_final | Blends ECE-calibrator output with market prob in logit space |
| B. Confidence Engine | ensemble, market_reliability, F10, S, T_mkt | C_pred, C_mkt, C_combined | Model Stack variance + Phase 6 market_reliability |
| C. Decision Score v3 | p_final, EV, e_clv, T_mkt, S, regime | DS3 | Extends DS v2 with market multipliers |
| D. Dynamic Threshold v2 | league / regime / market profiles | T_adj, quad-gate | Phase 6 triple-gate -> quad-gate |
| E. Risk Engine | line volatility, PnL series, freshness | V, D, Lambda | market velocity/volatility (Phase 6) |
| F. Portfolio Layer | DS3, C_combined, risk scores | stake, exposure | Kelly + daily/league/market caps |
| G. Feedback Integration | realized CLV, outcome | parameter updates | SHAP-CLV adaptation + safety-rollback |

---

## 2. Mathematics

### 2.1 Notation

| Symbol | Meaning | Range |
|---|---|---|
| $p_{raw}$ | raw model probability | [0,1] |
| $p_{cal}$ | ECE-minimizer calibrated probability (existing) | [0,1] |
| $p_{mkt}$ | de-vigged market (closing/consensus) probability | [0,1] |
| $p_{final}$ | market-aware final probability | [0,1] |
| $E_{lg}, E_{bk}$ | league / bookmaker efficiency score (Phase 6) | [0,1] |
| $T_{mkt}, T_{bk}, T_{lg}$ | market / bookmaker / league trust score (Phase 6) | [0,1] |
| $S$ | sharp confidence score (Phase 6) | [0,1] |
| $\hat{e}_{clv}$ | expected CLV (Phase 6 v2, probability space) | [-1,1] |
| $R$ | regime class in {R_ES, R_EV, R_IS, R_IV} | - |

### 2.2 Market-Aware Calibration Layer

Blending is done **in logit space** (linear blending in probability space distorts the tails):

$$ z_{model} = \operatorname{logit}(p_{cal}), \qquad z_{mkt} = \operatorname{logit}(p_{mkt}) $$

$$ z_{final} = (1-\lambda)\, z_{model} + \lambda\, z_{mkt}, \qquad p_{final} = \sigma(z_{final}) $$

The market weight $\lambda$ is not a single scalar; it is the product of efficiency, trust, sharp and the model's CLV credit:

$$ \lambda = \operatorname{clip}\!\Big( \lambda_{base}(R)\cdot w_{eff}\cdot w_{trust}\cdot w_{sharp}\cdot (1 - \rho\,\hat{e}_{clv}^{+}),\; 0,\; \lambda_{cap}\Big) $$

$$ w_{eff} = \sqrt{E_{lg}\,E_{bk}}, \qquad w_{trust} = T_{mkt}, \qquad w_{sharp} = 0.5 + 0.5\,S $$

where $\hat{e}_{clv}^{+}=\max(0,\hat{e}_{clv})$ is the model's expectation of beating the closing line: if the model side's expected CLV is high, $\lambda$ is reduced (trust the model). $\lambda_{cap}\le 0.8$ ensures the market never fully overrides the model.

**Decision rule — when to trust whom:**

| Condition | Effect | Rationale |
|---|---|---|
| Inefficient market ($E\downarrow$), low $T_{mkt}$ | $\lambda\downarrow$ -> trust model | Market price is noisy; model carries edge |
| High $\hat{e}_{clv}$ (model beats closing) | $\lambda\downarrow$ -> trust model | Model's historical deviation earns CLV |
| Efficient + stable market, high $T_{mkt}$ | $\lambda\uparrow$ -> trust market | Closing price is sharp; deviation is usually error |
| High sharp confidence $S$ with direction confirm | $\lambda\uparrow$ -> trust market | Sharp money direction sets the closing |

### 2.3 Confidence Engine

**Prediction confidence** (from the model):

$$ C_{pred} = \sigma\!\Big( a_1\,\underbrace{2\,|p_{final}-0.5|}_{\text{edge margin}} + a_2\,(1-\hat{\sigma}_{ens}) + a_3\,\text{rel}_{cal} \Big) $$

$\hat{\sigma}_{ens}$ = normalized Model Stack ensemble standard deviation, $\text{rel}_{cal}$ = local calibration reliability in that bin.

**Market confidence** (from the market):

$$ C_{mkt} = \sigma\!\Big( b_1\,\text{market\_reliability} + b_2\,(1-\hat{D}_{10}) + b_3\,S + b_4\,T_{mkt} \Big) $$

$\hat{D}_{10}$ = normalized inter-bookmaker disagreement (F10), $\text{market\_reliability}=0.30\,\text{liq}+0.25\,\text{cons}+0.25\,\text{cov}+0.20\,\text{fresh}$ (Phase 6).

**Combined confidence** — modulated by model-market agreement:

$$ A = 1 - |p_{cal} - p_{mkt}| \quad(\text{agreement factor}) $$

$$ C_{combined} = \big(C_{pred}^{\,w_p}\, C_{mkt}^{\,w_m}\big)^{\frac{1}{w_p+w_m}} \cdot \Big(A + (1-A)\,\hat{e}_{clv}^{+}\Big) $$

When model and market agree ($A\to1$) confidence rises; when they disagree confidence falls — **unless** the model has positive expected CLV ($\hat{e}_{clv}^{+}>0$), in which case the penalty is relaxed (deliberate, profitable dissent).

### 2.4 Decision Score v3

EV (back bet, decimal odds $o$):

$$ \text{EV} = p_{final}\cdot o - 1, \qquad \text{Edge} = p_{final} - p_{mkt} $$

Decision Score v3 — additive core x multiplicative market-gate x regime multiplier:

$$ \mathrm{DS}_3 = M_{reg}(R)\cdot\Big(\beta_1\,\widehat{\text{EV}} + \beta_2\,\hat{e}_{clv} + \beta_3\,\widehat{\text{Edge}} + \beta_4\,C_{combined}\Big)\cdot\Phi(T_{mkt},S) $$

$$ \Phi(T_{mkt},S) = T_{mkt}^{\,\gamma_t}\,(0.5+0.5\,S)^{\,\gamma_s}, \qquad \sum_i \beta_i = 1 $$

$\widehat{\cdot}$ = robust z-score / quantile normalization. The $\Phi$ factor automatically suppresses high EV when trust/sharp are low (multiplicative veto). Regime multiplier:

| Regime | $M_{reg}$ | Rationale |
|---|---|---|
| R_IS - inefficient-stable | 1.15 | Exploitable edge, low noise -> aggressive |
| R_ES - efficient-stable | 1.00 | Rare but reliable edge |
| R_EV - efficient-volatile | 0.80 | Sharp price + noise -> caution |
| R_IV - inefficient-volatile | 0.70 | Edge exists but fragile -> most cautious |

### 2.5 Dynamic Threshold Engine v2

Adaptive threshold = base + three offsets:

$$ T_{adj} = T_{base} + a\,(\bar{E}-E_{lg}) + b\,\Delta_{R} + c\,\Delta_{mkt} - d\,\hat{e}_{clv}^{+} $$

- **League-specific:** $a(\bar{E}-E_{lg})$ -> threshold *rises* in inefficient leagues (more selective); further tuned by league historical ROI/CLV.
- **Regime-specific:** $\Delta_R$ -> threshold rises in volatile regimes.
- **Market-specific:** $\Delta_{mkt}$ -> 1X2 / O-U / AH difficulty profile.
- $-d\,\hat{e}_{clv}^{+}$ -> strong CLV expectation lowers the threshold (increases volume).

Thresholds are solved with Optuna using a **hysteresis band** $[\,T_{adj}-\epsilon,\ T_{adj}+\epsilon\,]$ (prevents bet on/off flip-flop at regime boundaries).

**Quad-gate** (extension of the Phase 6 triple-gate) — bet only if all gates open:

$$ \mathrm{DS}_3 \ge \tau_{DS} \;\wedge\; p_{final}\ge T_{adj} \;\wedge\; \text{Edge}\ge \text{Edge}_{min} \;\wedge\; \hat{e}_{clv}\ge \tau_{clv} $$

### 2.6 Risk Engine

**Volatility score** (market + model):

$$ V = \omega_1\,\widehat{\operatorname{Var}}(\Delta\text{line}) + \omega_2\,\text{market\_velocity} + \omega_3\,\hat{\sigma}_{ens} $$

**Drawdown risk score** (EWMA of negative PnL + streak):

$$ D = \sigma\!\Big( c_1\,\frac{\text{DD}_{cur}}{\text{DD}_{max}} + c_2\,\text{loss\_streak} + c_3\,(1-\overline{\text{rCLV}}_{EWMA}) \Big) $$

**Confidence decay score** (signal staleness):

$$ \Lambda = 1 - e^{-\kappa\,\Delta t}\cdot(1-\text{drift}) \cdot (1 - P_{\text{regime-shift}}) $$

$\Delta t$ = time to closing/kickoff, $\text{drift}$ = data drift (drop in calibration reliability), $P_{\text{regime-shift}}$ = Phase 6 regime transition probability.

### 2.7 Portfolio Layer

Fractional Kelly, scaled by confidence and risk:

$$ f_{kelly} = \frac{p_{final}\,o - 1}{o - 1}, \qquad f^{*} = \kappa\,f_{kelly}\cdot C_{combined}\cdot (1 - \eta_V V)(1 - \eta_D D)(1 - \eta_\Lambda \Lambda) $$

$\kappa\approx 0.25$ (quarter-Kelly). **Exposure caps** (as a percent of bankroll):

$$ \sum_{\text{day}} f^{*} \le L_{day}, \qquad \sum_{\text{league}} f^{*} \le L_{lg}, \qquad \sum_{\text{market}} f^{*} \le L_{mkt} $$

Correlation adjustment: same-match / same-day-same-league bets are treated as correlated; joint risk is bounded via $\sqrt{\mathbf{f}^\top \Sigma\, \mathbf{f}}$. On cap breach, stakes are proportionally trimmed by $\mathrm{DS}_3$ priority.

---

## 3. Integration Framework

How Phase 6 market intelligence connects to the four existing layers:

| Layer | Phase 7 integration |
|---|---|
| Prediction Layer | Model Stack output $p_{raw}$ stays unchanged; market features (F07/F08/F10/F17) are already inside as features. Ensemble variance feeds the Confidence Engine. |
| Calibration Layer | ECE-minimizer produces $p_{cal}$, then Market-Aware Calibration blends to $p_{final}$. The existing calibrator is not removed; a blend layer is added on top. |
| Threshold Layer | Fixed/league threshold -> Dynamic Threshold v2 (league+regime+market offset, hysteresis). Triple-gate -> quad-gate. |
| CLV Layer | $\hat{e}_{clv}$ feeds $\lambda$ (calibration), $T_{adj}$ (threshold) and $\mathrm{DS}_3$; realized CLV drives the feedback loop. |

---

## 4. Decision Logic

End-to-end flow per match:

1. **Predict** -> $p_{raw}$ (Model Stack).
2. **Calibrate** -> $p_{cal}$ (ECE-minimizer) -> $p_{final}$ (logit blend, $\lambda$).
3. **Confidence** -> $C_{pred}, C_{mkt}, C_{combined}$.
4. **Score** -> $\mathrm{DS}_3$ (regime multiplier + $\Phi$ veto).
5. **Threshold / Quad-gate** -> all four conditions open? If not, **PASS**.
6. **Risk** -> compute $V, D, \Lambda$; on extreme risk stake = 0 (soft veto).
7. **Portfolio** -> stake $f^{*}$; apply daily/league/market caps.
8. **Bet or Pass**, logging the decision and all intermediate scores (audit + feedback).

---

## 5. Risk Controls

- **Multiplicative veto:** $\Phi$ and the risk multipliers let a single weak signal (low trust / high volatility) nullify a high EV.
- **Quad-gate:** no bet unless probability, edge, CLV and score are all above threshold simultaneously.
- **Exposure caps + correlation:** daily/league/market and correlated-bet limits prevent cluster risk.
- **Confidence decay:** stale or likely regime-shift signals are auto-suppressed.
- **Safety rollback (existing):** feedback updates run in shadow-mode before going live; auto-revert on metric degradation.
- **Hysteresis:** the threshold band prevents flip-flop at regime boundaries.

---

## 6. Feedback Integration

Realized CLV + outcome update four targets in a closed loop (all rate-limited + rollback-protected):

| Target | Update rule |
|---|---|
| Thresholds | If realized CLV stays positive above the band -> $T_{adj}\downarrow$ (open volume); if negative -> $T_{adj}\uparrow$. Gradient/bandit step with a small learning rate. |
| Calibration | SHAP-CLV adaptation extended: $\Delta\lambda = \alpha\cdot \operatorname{sign}(\text{rCLV}_{mkt}-\text{rCLV}_{model})$ — if trusting the market would have earned more CLV, $\lambda\uparrow$. Periodic recalibration with outcome + CLV. |
| Market Trust | $T_{mkt},T_{bk},T_{lg}$ updated via EWMA on whether closing beat the model (Phase 6 Trust Engine). |
| Regime Models | Regime transition probabilities and regime-specific params ($M_{reg}, \lambda_{base}, \Delta_R$) updated from realized volatility/efficiency. |

---

## 7. Implementation Roadmap

| Step | Work package | Dependency |
|---|---|---|
| 7.1 | $p_{mkt}$ de-vig + logit-blend infrastructure (Market-Aware Calibration) | Phase 6 efficiency/trust |
| 7.2 | Confidence Engine ($C_{pred}, C_{mkt}, C_{combined}$) | Model Stack variance, market_reliability |
| 7.3 | Decision Score v3 + $\Phi$ veto + $M_{reg}$ | 7.1, 7.2 |
| 7.4 | Dynamic Threshold v2 + quad-gate + hysteresis (Optuna) | 7.3 |
| 7.5 | Risk Engine ($V, D, \Lambda$) | Phase 6 velocity/volatility |
| 7.6 | Portfolio Layer (fractional Kelly + exposure caps + correlation) | 7.4, 7.5 |
| 7.7 | Feedback Integration (4 targets, shadow-mode + rollback) | 7.1-7.6 |
| 7.8 | Backtest + walk-forward + A/B (DS v2 vs v3) | all |

**Estimated duration:** ~12 weeks. Critical path: 7.1 -> 7.3 -> 7.4 -> 7.6 -> 7.7. Steps 7.2 and 7.5 can run in parallel.

---

## Expected Impact

- **Sharper calibration:** leaning on the market in efficient markets lowers ECE; trusting the model in inefficient markets preserves edge.
- **Cleaner selection:** quad-gate + $\Phi$ veto eliminate low-confidence high-EV traps -> higher risk-adjusted return (mean CLV up, variance down).
- **Capital protection:** exposure caps + drawdown/decay vetoes automatically cut exposure during bad runs.
- **Self-correcting system:** the CLV feedback loop tunes threshold/calibration/trust/regime params on live data; safe via safety-rollback.
