# Adaptive Threshold & Decision Optimization System

This document details the Optuna-based joint optimization parameters, utility score formulas, database-backed state stores, and safety rollback triggers.

---

## 1. Decision Score & Multicriteria Selection

To decide whether a candidate prediction is playable, the engine computes a **Decision Score** that blends probabilities, edges, and risk factors:

$$\text{DecisionScore} = \text{ProbabilityScore} + \text{ValueScore} + \text{CLVHistoryScore} + \text{CLVFeedbackScore} + \text{MarketBiasAdjustment} - \text{RiskPenalty}$$

Where:
- $\text{ProbabilityScore}$ combines probability, sample sizes, and coverage factors.
- $\text{ValueScore}$ scales edge by $1.2$.
- $\text{CLVHistoryScore}$ adds $0.5 \times (\text{AvgCLV} / 100)$.
- $\text{CLVFeedbackScore}$ accumulates feature weight $\times$ SHAP products.
- $\text{MarketBiasAdjustment}$ adds $0.1 \times (\text{Bias} - 0.5)$.
- $\text{RiskPenalty}$ subtracts points for derbies, player absences, and base model disagreements.

A bet is recommended (`PLAY`) if the selection satisfies the **double-gate filter**:
$$\text{Edge} \ge 2\% \quad \text{and} \quad \text{Calibrated Probability} \ge \text{Adjusted Threshold}$$

---

## 2. Joint Optimization Formulation

The baseline thresholds are optimized using **Optuna** to maximize returns, line movements, and edge margins.

### I. Optimization Objective Function
The optimizer searches for the best parameters to maximize the composite utility score:

$$\text{Score} = (0.4 \times \text{ROI}) + (0.3 \times \text{CLV}) + (0.2 \times \text{Edge}) - (0.1 \times \text{CoveragePenalty})$$

- $\text{CoveragePenalty}$: Applied if selection coverage (played picks / total available) falls below $10\%$:
  $$\text{CoveragePenalty} = \begin{cases} 
      10.0 \times (0.10 - \text{coverage}) & \text{if coverage} < 0.10 \\
      0 & \text{otherwise}
  \end{cases}$$
- **Drawdown Constraint:** Any simulated trial resulting in a maximum drawdown of $\ge 15\%$ flat units is automatically disqualified ($\text{Score} = -999.0$).

### II. Parameter Search Space
- **Base Thresholds ($T_{\text{base}}$):** Outcome selection boundaries.
  - Draw outcomes: $[0.28, 0.45]$ (Search range reflecting lower draw densities)
  - Other outcomes (1, 2, 1X, etc.): $[0.55, 0.80]$
- **Market Multipliers ($M_{\text{market}}$):**
  - Match Result (MS): $[1.00, 1.10]$
  - Double Chance (DC): $[0.95, 1.02]$
  - Draw No Bet (DNB): $[0.85, 0.95]$
- **Final Threshold Applied:**
  $$\text{Threshold}_{\text{final}} = T_{\text{base}} \times M_{\text{market}}$$

---

## 3. Database State Store & Versioning

Optimized configurations are stored in the database `threshold_state` table:
- **Keys:** `league_id` (specific code or league type) and `market_type` (selections like "1", "1X", "DNB1").
- **Fields:** `threshold_value`, `roi_30d`, `clv_30d`, `coverage_30d`, `version`, `is_active`.
- Every optimization run deactivates the current active configuration (`is_active = 0`) and inserts a new incremented configuration version with `is_active = 1`.

---

## 4. Safety Rollbacks & Micro-adjustments

To protect trading capital, the runner executes two online loops:

1. **Daily Micro-adjustments:**
   - If rolling 7-day CLV improves, it slightly lowers thresholds by $-0.002$ (allowing more plays).
   - If rolling 7-day ROI drops, it increases thresholds by $+0.005$ (raising selectivity).
2. **Safety Rollback Guard:**
   - Evaluates the rolling 7-day ROI of each league (requiring at least 3 bets).
   - If rolling ROI drops below $-10\%$, the engine automatically sets `is_active = 0` on the current version, queries previous versions for the one with the highest `roi_30d`, and reactivates it (`is_active = 1`).
