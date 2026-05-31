# Closing Line Value (CLV) & Value Betting Engine

This document details the overround margin cleaning formulas, expected value (EV) calculations, CLV line movement monitoring, and the adaptive learning feedback engine.

---

## 1. Bookmaker Overround Margin Cleaning

Raw bookmaker odds contain a profit margin (overround). To extract the market's true implied probability, the system cleans the overround using the **proportional method**:

1. Calculate raw implied probabilities:
   $$P_{\text{raw}, i} = \frac{1}{\text{Odds}_i}$$
2. Sum the raw probabilities to find the overround margin:
   $$\text{Margin} = \sum_{i} P_{\text{raw}, i}$$
3. Clean the probabilities:
   $$P_{\text{clean}, i} = \frac{P_{\text{raw}, i}}{\text{Margin}}$$

This ensures $P_{\text{clean}, \text{home}} + P_{\text{clean}, \text{draw}} + P_{\text{clean}, \text{away}} = 1.0$, removing bookmaker bias.

---

## 2. Value Bet Detection & Edge Classification

A value bet exists if the model's calibrated probability ($P_{\text{model}}$) exceeds the clean market probability ($P_{\text{clean}}$) by at least $2\%$:

$$\text{Edge} = P_{\text{model}} - P_{\text{clean}}$$

### Value Edge Classifications:
- `NO_VALUE`: $\text{Edge} < 2\%$ (recommendation skipped)
- `LOW_VALUE`: $2\% \le \text{Edge} < 5\%$
- `MEDIUM_VALUE`: $5\% \le \text{Edge} < 8\%$
- `HIGH_VALUE`: $\text{Edge} \ge 8\%$

---

## 3. Closing Line Value (CLV) Monitoring

CLV measures prediction quality against final pre-kickoff market consensus.

### Line Movement Percentage:
$$\text{CLV (\%)} = \frac{\text{Closing Odds} - \text{Prediction Odds}}{\text{Prediction Odds}} \times 100$$

### CLV Classifications:
- `STRONG_POSITIVE_CLV`: $\text{CLV} \ge 10.0\%$ (significant market steam in model direction)
- `POSITIVE_CLV`: $2.0\% \le \text{CLV} < 10.0\%$
- `NEUTRAL_CLV`: $-2.0\% < \text{CLV} < 2.0\%$
- `NEGATIVE_CLV`: $-10.0\% < \text{CLV} \le -2.0\%$
- `STRONG_NEGATIVE_CLV`: $\text{CLV} \le -10.0\%$

---

## 4. Adaptive Learning Feedback Loops

Instead of just reporting CLV, the system uses CLV signals at kickoff to tune the **Decision Layer** dynamically:

### I. SHAP-CLV Feature Weight Adaptation
When kickoff odds reconcile, the engine extracts the match's SHAP values. If the sign of the SHAP value aligns with the sign of the CLV percentage (signifying the feature contributed to a value pick the market eventually backed), the feature weight increases:

$$\Delta w_f = 0.01 \times \text{sign}(\text{SHAP}_f) \times \text{sign}(\text{CLV}_{\%})$$

Weights are clamped within $[-1.0, 1.0]$ and stored in `dynamic_feature_weights.json`.

### II. Market Bias Updates
Pushes market biases up by $+0.01$ (positive CLV) or down by $-0.01$ (negative CLV). Clamped within $[0.0, 1.0]$.

### III. Threshold Micro-adjustments
Queries rolling CLV for a league over the last 10 predictions. If $\text{rolling\_clv} < 0$, outcome thresholds increment by $+0.01$ (more selective). If $\text{rolling\_clv} > 5.0\%$, thresholds decrement by $-0.005$ (allowing more bets).
