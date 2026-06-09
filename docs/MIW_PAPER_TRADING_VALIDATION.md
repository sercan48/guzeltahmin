# MIW Phase 11 — Paper Trading & Execution Validation Engine

> **Scope.** A validation layer that sits between the Decision Engine and **Real-Money Deployment**. Purpose: validate execution quality, signal quality, bankroll/portfolio behavior, and CLV consistency **without risking real capital**. Previous phases are **not redesigned**; this is only the validation gate between them. **No code.**

---

## 1. Executive Summary

The system is designed and equipped with a reality filter (Phase 10); what is needed now is **proof**: demonstrating, without real money, that signals and execution are genuinely profitable under live conditions. Phase 11 produces three layers of proof:

1. **Paper Trading** — each signal is simulated through a full lifecycle (state machine + audit trail); the execution simulator applies odds drift, fill, and slippage.
2. **Shadow Mode** — the system runs live but **places no real bets**; predicted vs actual profit and CLV are tracked side by side.
3. **Deployment Readiness Score (0-100)** — an 8-component gate; if any component is below its floor, `LIVE_DEPLOYMENT_READY = NO`.

Then **Go-Live**: Paper -> Micro -> Controlled -> Scaled, each transition with explicit promotion/rollback criteria.

**Final Readiness Score (current design stage): 41/100 -> LIVE_DEPLOYMENT_READY = NO** (no paper/shadow data yet; the score only rises once the validation window is complete).

---

## 2. Paper Trading Architecture (Module 1 — Bet Lifecycle)

### 2.1 State machine
```
SIGNAL_CREATED -> SIGNAL_ACCEPTED -> PAPER_BET_PLACED -> MATCH_STARTED
   -> MATCH_FINISHED -> BET_SETTLED -> POST_MATCH_ANALYSIS
```
Reject/cancel paths: `SIGNAL_CREATED->REJECTED` (failed gate), `SIGNAL_ACCEPTED->EXPIRED` (no fill/odds gone), `PAPER_BET_PLACED->VOID` (match postponed/cancelled).

| State | Timestamp | Trigger | Data written |
|---|---|---|---|
| SIGNAL_CREATED | $t_{signal}$ | Decision engine DS v3 + gate | model p, p_cal, Edge, DS, regime, snapshot ref |
| SIGNAL_ACCEPTED | $t_{accept}$ | Risk/portfolio approval (exposure, Kelly stake) | stake, exposure impact, correlation check |
| PAPER_BET_PLACED | $t_{place}$ | Execution simulator | quoted vs executed odds, fill prob, slippage |
| MATCH_STARTED | $t_{kickoff}$ | Kickoff event | closing odds, sharp fair (Phase 10) |
| MATCH_FINISHED | $t_{final}$ | Match end | score, outcome |
| BET_SETTLED | $t_{settle}$ | Settlement | P&L, realized odds return |
| POST_MATCH_ANALYSIS | $t_{analysis}$ | Analysis job | CLV/corrected/exec-adjusted, SQS, attribution |

### 2.2 Audit trail
- **Append-only, immutable event log**: each transition is an event; `(bet_id, from_state, to_state, ts, actor, reason, data_snapshot_hash)`.
- No retroactive edits; corrections are made via new events (event sourcing).
- Each signal links to the snapshot hash at decision time -> cross-verifiable with the leakage audit (Phase 10).

---

## 3. Execution Simulator (Module 2)

### 3.1 Odds drift (signal_time -> execution_time)
| Market | Drift model | Character |
|---|---|---|
| Stable | Ornstein-Uhlenbeck (mean-reverting), low $\sigma$ | Small, pulling-back movement |
| Volatile | High $\sigma$ random walk + jump component | Wide, unpredictable swings |
| Steam | Directional drift $\mu<0$ (odds drop) + momentum | One-directional, fast close |

The regime is selected from MIW steam/RLM/volatility features (consistent with Phase 10).

### 3.2 Fill probability
$$ P_{fill} = \sigma\Big(a - b\,\tfrac{o_{req}-o_{mkt}}{\theta} - c\,\Delta_{move} - d\,\tfrac{s}{L_{mkt}} + e\,\mathbb{1}[\text{liquid market}]\Big) $$
Market type (1X2 > O/U > AH > prop), liquidity, odds movement, and stake size all enter.

### 3.3 Slippage (for every paper bet)
$$ o^{exec} = (o^{quote} - \delta_{drift})(1 - \text{slip}(s, L_{mkt})), \qquad \text{slip} = \kappa\,\frac{s}{L_{mkt}} $$
For every bet, `quoted_odds`, `executed_odds`, `fill_prob`, `matched_stake` are recorded -> feeds Phase 10 execution-adjusted CLV.

---

## 4. Signal Quality Framework (Module 4 + 6)

### 4.1 Per-signal metrics
| Group | Metrics |
|---|---|
| Accuracy | Prediction accuracy, Brier score, Log loss, Calibration error (ECE/segment) |
| CLV | CLV, Corrected CLV, Execution-Adjusted CLV (Phase 10) |
| Value | Expected Value, Realized Value, EV-RV divergence |

### 4.2 Signal Quality Score (SQS)
All components standardized via z-score/rank and scaled to 0-100:
$$ SQS = 100\cdot\sigma\big(w_1 z_{acc} + w_2 z_{CLV^{corr}} + w_3 z_{ROI} + w_4 z_{stab} + w_5 z_{conf}\big) $$
$z_{stab}$ = consistency of the signal-group return (low variance -> high); $z_{conf}$ = C_combined (Phase 7).

### 4.3 Decision Engine Validation (Module 6 — attribution)
Each bet's P&L is decomposed into DS v3 components (Shapley-style contribution):
$$ \text{PnL}_i = \phi^{EV}_i + \phi^{\hat e_{clv}}_i + \phi^{Edge}_i + \phi^{C}_i + \phi^{trust}_i + \phi^{sharp}_i + \phi^{regime}_i + \varepsilon_i $$
Aggregation -> a table of **which component produces the largest share of profit / loss**. A component that feeds profit negatively (e.g., the regime filter in a certain regime) is flagged for shrinking.

---

## 5. Portfolio Validation Framework (Module 3 + 5)

### 5.1 Paper Portfolio (Module 3)
**Bankroll:** `starting`, `current`, `peak` (high-water), `effective` ($B_{eff}=B\cdot\varphi(DD)$, Phase 8 ratchet).

**Drawdown:** daily / weekly / monthly + max; $DD_t = (H_t - B_t)/H_t$.

**Growth metrics:**
| Metric | Formula |
|---|---|
| ROI | $\text{net profit} / \text{deployed bankroll}$ |
| Yield | $\text{net profit} / \sum \text{stake (turnover)}$ |
| CAGR | $(B_{end}/B_{start})^{365/\text{days}} - 1$ |
| Profit Factor | $\sum \text{wins} / \sum |\text{losses}|$ |
| Expectancy | $p_{win}\cdot \overline{win} - p_{loss}\cdot \overline{loss}$ (per bet) |

### 5.2 Portfolio Validation (Module 5)
- **Exposure:** by league / market / day / week; realized usage against Phase 8 limits.
- **Correlation:** same-match / same-league / same-market / same-day; realized vs Phase 8 $\rho$ prior.
- **Risk:** portfolio volatility $\sigma_p=\sqrt{s^\top\Sigma s}$; $\text{VaR}_\alpha$; $\text{ES}_\alpha = \mathbb{E}[L\mid L>\text{VaR}_\alpha]$. The realized distribution is compared to the predicted one (calibration).

---

## 6. Shadow Mode Architecture (Module 7)

The system runs **live** (real snapshots, real decisions, real timing) but **places no real bets** — everything is simulated.

| Tracked | Definition |
|---|---|
| Predicted profit | Expected P&L from the EV at decision time |
| Actual profit | Realized P&L after execution-sim |
| CLV | Sharp-anchored + corrected + exec-adjusted (Phase 10) |
| Execution quality | fill rate, average slippage, missed-bet % |

**Minimum validation window (before going to real money):**
- At least **8-12 weeks** *or* **>= 500 settled paper/shadow bets** (whichever is later),
- >= 2 different market regimes (efficient + inefficient) covering most target leagues,
- stable (drift-free) corrected-CLV and realized~predicted convergence.

---

## 7. Deployment Readiness Model (Module 8)

$$ DRS = \sum_{k} w_k\, c_k \quad (0\text{-}100),\qquad \sum_k w_k = 1 $$

| Component $c_k$ | Weight | Floor |
|---|---|---|
| Prediction Reliability | 0.15 | >= 60 |
| Calibration Quality | 0.15 | >= 65 |
| Execution Quality | 0.15 | >= 60 |
| CLV Stability | 0.20 | >= 65 |
| Portfolio Stability | 0.10 | >= 55 |
| Drawdown Control | 0.10 | >= 60 |
| Data Integrity | 0.10 | >= 70 |
| Market Coverage | 0.05 | >= 50 |

**Gate rule:**
$$ \text{LIVE\_DEPLOYMENT\_READY} = \text{YES} \iff DRS \ge 75 \ \wedge\ (\forall k:\ c_k \ge \text{floor}_k)\ \wedge\ \text{shadow window complete} $$
If a single component is below its floor, the answer is **NO** even if DRS is high.

---

## 8. Failure Detection System (Module 9)

| Failure | Early warning indicator |
|---|---|
| False CLV | Corrected-CLV+ but realized ROI- (persistent divergence); sharp-anchor deviation |
| Overfitted thresholds | Walk-forward ROI << in-sample; performance cliff around the threshold |
| Regime misclassification | Regime transition frequency anomaly; regime-conditional ROI conflicting with expectation |
| Execution failures | Fill rate drop; rising average slippage; climbing missed-bet % |
| Liquidity traps | Concentration in low $L_{mkt}$; stake/liquidity ratio exceeding threshold |
| Portfolio concentration | HHI increase; single league/market/day exposure converging to cap |
| Paper/live divergence | Shadow predicted-actual gap band breach; CLV distribution shift (PSI) |

Each indicator has green/yellow/red thresholds; red -> automatic go-live brake.

---

## 9. Go-Live Framework (Module 10)

| Phase | Capital | Required CLV | Required ROI | Max DD | Promotion | Rollback |
|---|---|---|---|---|---|---|
| A — Paper | 0 (sim) | corrected-CLV+ stable, >= 500 bets | Yield > 0 | - | DRS >= 75 + all floors | DRS < 60 |
| B — Micro | very small (e.g. 1% B) | >= +1% mean corrected-CLV | >= 70% of paper | <= 8% | 4-6 wk + DD control | DD > 12% or CLV->negative |
| C — Controlled | medium (e.g. 25% B) | >= +1.5% stable CLV | >= 80% of paper | <= 12% | 8-12 wk + Sharpe threshold | DD > 18% or paper/live divergence |
| D — Scaled | full Kelly-Phase 8 | persistent +CLV | target ROI | <= Phase 8 halt | - (full production) | circuit breaker (Phase 8 DD_halt) |

Each transition is not one-way: if a rollback criterion triggers, it drops to the previous phase.

---

## 10. Implementation Roadmap

| Step | Deliverable | Dependency | Risk |
|---|---|---|---|
| 11.1 | Paper bet lifecycle state machine + append-only audit log | Decision engine (Phase 7) | Low |
| 11.2 | Execution simulator (drift regimes + fill + slippage) | Phase 10 | Medium |
| 11.3 | Paper portfolio engine (bankroll/DD/growth metrics) | Phase 8 | Low |
| 11.4 | Signal Quality Engine + SQS + DS v3 attribution | 11.1, Phase 7 | Medium |
| 11.5 | Portfolio validation (exposure/correlation/VaR/ES) | 11.3, Phase 8 | Medium |
| 11.6 | Shadow mode (live, zero-bet) + predicted vs actual | 11.2-11.5 | High |
| 11.7 | Deployment Readiness Score + LIVE gate | 11.4-11.6 | Medium |
| 11.8 | Failure detection + early-warning dashboard | 11.1-11.7 | Low |
| 11.9 | Go-live framework (A->B->C->D promotion/rollback) | 11.7,11.8 | Medium |

**Estimated duration (build):** ~6-8 weeks. **Validation window (usage):** an additional 8-12 weeks of paper+shadow.

---

## 11. Final Readiness Score

| Component | Current estimate | Note |
|---|---|---|
| Prediction Reliability | 62 | Stack strong; no live validation |
| Calibration Quality | 58 | Segment calibration not yet applied (audit #3) |
| Execution Quality | 30 | Sim exists, no real fill data |
| CLV Stability | 34 | Sharp-anchor is design; no live series |
| Portfolio Stability | 40 | Paper portfolio not run |
| Drawdown Control | 45 | Phase 8 mechanism exists; no live test |
| Data Integrity | 55 | Anti-leakage audit (Phase 10) not yet run |
| Market Coverage | 40 | Warehouse at MVP stage |
| **Weighted DRS** | **41 / 100** | **LIVE_DEPLOYMENT_READY = NO** |

> **Decision: LIVE_DEPLOYMENT_READY = NO.** Not because the score is low — but because **proof has not been produced yet**. As these engines run and the 8-12 week paper+shadow window fills, the Execution/CLV/Portfolio components rise with real data. While even a single component is below its floor (currently most of them), there is **no transition** to real money.
