# MIW Phase 8 — Portfolio Intelligence & Capital Allocation Engine

> **Scope.** Phase 8 turns the single-bet decisions from Phase 7 (p_final, DS3, quad-gate, risk scores) into **portfolio-level** capital allocation. This document is **design only — no code**. It is compatible with the existing system: fractional Kelly, exposure caps, the correlation matrix $\Sigma$, the safety-rollback and the Optuna utility are preserved; Phase 8 extends them into a full portfolio optimization layer.

---

## 1. Architecture

```
Candidate bets (quad-gate pass)  --> {p_final, EV, e_clv, C_combined, V, D, Lambda}
        |
        v
[1] Bankroll Manager --> B_eff (high-water + drawdown ratchet)
        |
        v
[2] Fractional Kelly Framework --> raw f_kelly (single + simultaneous)
        |
        v
[4] Correlation Detection --> Sigma (covariance / clustering)
        |
        v
[9] League Allocation  +
[10] Market Allocation  --> allocation weights A_lg, A_mkt
        |
        v
[Portfolio Optimizer] max  EV_p + mu*eclv_p - (gamma/2)*sigma_p^2
        |   s.t. [3] exposure caps, [7] daily, [8] weekly budget
        v
[5] Portfolio Risk Score + [6] Drawdown Protection (circuit breaker)
        |
        v
   Final stake vector  s*  --> metrics: C_p, EV_p, eclv_p, Sharpe_p
```

| Module | Responsibility | Link to existing system |
|---|---|---|
| 1. Bankroll Manager | Effective capital, high-water mark, ratchet | New; consumes PnL series |
| 2. Fractional Kelly | Single + simultaneous growth-optimal stake | Phase 7 $f^{*}$ formula |
| 3. Exposure Limits | Single/match/league/market/day caps | Phase 7 $L_{day},L_{lg},L_{mkt}$ |
| 4. Correlation Detection | $\Sigma$ covariance + clustering | Phase 7 correlation adjustment |
| 5. Portfolio Risk Score | Aggregate risk measure (PRS) | Aggregates Phase 7 V/D/Lambda |
| 6. Drawdown Protection | De-risk ratchet + circuit breaker | Compatible with safety-rollback |
| 7. Daily Risk Budget | Daily expected-loss / VaR budget | New |
| 8. Weekly Risk Budget | Rolling weekly budget + carryover | New |
| 9. League Allocation | Capital split across leagues | Phase 6 $E_{lg},T_{lg}$ |
| 10. Market Allocation | Split across market types | Phase 6 liquidity/efficiency |

---

## 2. Mathematics

### 2.1 Notation

| Symbol | Meaning |
|---|---|
| $B_t,\ B_{eff}$ | bankroll at t / effective (risk-adjusted) bankroll |
| $H_t,\ DD_t$ | high-water mark / current drawdown |
| $o_i,\ p_{final,i}$ | decimal odds / Phase 7 calibrated probability of bet i |
| $s_i,\ f_i$ | stake (money) / capital fraction of bet i |
| $\Sigma$ | covariance matrix of bet returns |
| $\kappa$ | Kelly fraction ($\approx 0.25$) |

### 2.2 Bankroll Management

$$ B_t = B_{t-1} + \sum_i s_{i,t-1}\,r_{i,t-1}, \qquad H_t = \max_{\tau\le t} B_\tau, \qquad DD_t = \frac{H_t - B_t}{H_t} $$

Effective capital is risk-adjusted by a **ratchet** that shrinks with drawdown:

$$ B_{eff} = B_t\cdot \phi(DD_t), \qquad \phi(DD) = (1 - DD)^{\,\psi}, \quad \psi \ge 1 $$

$\phi$ automatically cuts exposure as drawdown deepens; $\phi=1$ at $DD=0$. Stakes are always computed off $B_{eff}$ (compounding growth, but braking in drawdown).

### 2.3 Fractional Kelly Framework

Single bet:

$$ f_{kelly,i} = \frac{p_{final,i}\,o_i - 1}{o_i - 1} = \frac{\text{Edge}_i}{o_i - 1} $$

Multiple same-day bets -> **simultaneous (portfolio) Kelly**, growth-optimal:

$$ \max_{\mathbf f \ge 0}\; \mathbb{E}\!\left[\ln\!\Big(1 + \sum_i f_i\,X_i\Big)\right] \quad \text{s.t.}\ \sum_i f_i \le f_{max} $$

$X_i$ = unit return of bet i ($o_i-1$ if win, $-1$ if lose). Final stake, with the Phase 7 dampeners:

$$ s_i = \kappa\,f_{kelly,i}\,C_{combined,i}\,(1-\eta_V V_i)(1-\eta_D D)(1-\eta_\Lambda \Lambda_i)\,B_{eff} $$

### 2.4 Exposure Limits

$$ s_i \le c_{bet}B_{eff}, \quad \sum_{j\in m} s_j \le c_{match}B_{eff}, \quad \sum_{\text{league}} s \le L_{lg}, \quad \sum_{\text{market}} s \le L_{mkt}, \quad \sum_{\text{day}} s \le L_{day} $$

### 2.5 Correlation Detection

Portfolio variance:

$$ \sigma_p^2 = \mathbf{s}^\top \Sigma\, \mathbf{s}, \qquad \Sigma_{ij} = \rho_{ij}\,\sigma_i\sigma_j $$

$\rho_{ij}$ = structural prior + empirical blend (shrinkage):

| Relationship | $\rho$ prior | Reason |
|---|---|---|
| Same match, opposing outcomes | $\approx -1$ | Cannot both win (mutually exclusive) |
| Same day + same league | $>0$ | Common shocks (referee, weather, news) |
| Same market type, different match | small $>0$ | Shared model/feature error |
| Independent | $\approx 0$ | - |

Empirical $\rho$ is estimated from historical CLV/result co-movement and shrunk toward the structural prior (Ledoit-Wolf style). Correlated clusters are capped as a single "effective bet".

### 2.6 Portfolio Risk Score

$$ \mathrm{PRS} = \sigma\!\Big( \theta_1\,\widehat{\sigma_p} + \theta_2\,\mathrm{HHI}_{exp} + \theta_3\,DD_t + \theta_4\,U_{budget} \Big) $$

$\mathrm{HHI}_{exp}=\sum_k w_k^2$ (league/market concentration, Herfindahl), $U_{budget}$ = budget utilization. High PRS -> new stakes are globally scaled down.

### 2.7 Drawdown Protection

In addition to the ratchet $\phi(DD)$, a **circuit breaker**:

$$ DD_t \ge DD_{halt} \;\Rightarrow\; \text{new stake} = 0 \ \text{(cooldown + manual/auto review)} $$

Graduated de-risk: when $DD>DD_{warn}$, $\kappa \leftarrow \kappa\cdot(1-\zeta)$.

### 2.8 Daily & Weekly Risk Budget

Daily expected-loss / VaR budget:

$$ \text{Budget}_{day} = q_d\,B_{eff}, \qquad \sum_{\text{day}} \mathbb{E}[\text{Loss}_i] \le \text{Budget}_{day} \quad (\text{or } \mathrm{VaR}_\alpha \le \text{Budget}_{day}) $$

Rolling weekly budget with limited carryover:

$$ \text{Budget}_{week} = q_w\,B_{eff}, \qquad \text{unused daily carryover} \le \text{cap}_{carry} $$

$q_d < q_w$; if the weekly budget is exhausted, new risk halts until the end of the week.

### 2.9 League & Market Allocation

League allocation proportional to edge quality x capacity:

$$ A_{lg} \propto \text{quality}_{lg}\cdot \text{capacity}_{lg}, \qquad \text{quality}_{lg} = g\big(E_{lg}, T_{lg}, \overline{\text{rCLV}}_{lg}, \mathrm{ROI}_{lg}\big) $$

$$ \sum_{lg} A_{lg} = 1, \qquad A_{lg} \le A_{lg}^{cap} $$

Market allocation likewise by liquidity, efficiency and CLV reliability (1X2 / O-U / AH). Allocations set the $L_{lg}, L_{mkt}$ caps.

### 2.10 Portfolio Metrics

$$ \text{EV}_p = \sum_i s_i\,\text{EV}_i, \qquad \hat{e}_{clv,p} = \frac{\sum_i s_i\,\hat{e}_{clv,i}}{\sum_i s_i}, \qquad C_p = \frac{\sum_i s_i\,C_{combined,i}}{\sum_i s_i} $$

Sharpe-like metrics:

$$ \text{Sharpe}_p = \frac{\mathbb{E}[R_p]}{\sigma_p}, \qquad \text{CLV-Sharpe}_p = \frac{\overline{e}_{clv,p}}{\sigma(e_{clv})} $$

CLV-Sharpe is a lower-noise quality measure stripped of result variance (CLV is the leading indicator of ROI).

---

## 3. Risk Framework

- **Three braking layers:** (i) per-bet dampeners $(1-\eta V)(1-\eta D)(1-\eta\Lambda)$, (ii) portfolio variance penalty $\frac{\gamma}{2}\sigma_p^2$, (iii) budget + circuit breaker.
- **Concentration control:** HHI + correlation clusters prevent single-league/single-day over-exposure.
- **Drawdown ratchet:** $\phi(DD)$ shrinks exposure automatically with drawdown; full halt at $DD_{halt}$.
- **Budget discipline:** daily + weekly VaR/expected-loss caps with limited carryover.
- **safety-rollback (existing):** allocation/budget parameter updates validated in shadow-mode.

---

## 4. Allocation Framework

Hierarchical allocation: **Bankroll -> Weekly budget -> Daily budget -> League allocation -> Market allocation -> Single stake.** Each level inherits the cap of the level above; the lower level redistributes by edge quality. Unused allocation is transferred, in a limited way, to high-quality leagues/markets with remaining capacity.

---

## 5. Portfolio Optimization Logic

Daily solution = CLV-augmented mean-variance utility maximization:

$$ \max_{\mathbf{s}\ge 0}\; \underbrace{\mathbb{E}[R_p]}_{\text{return}} + \mu_{clv}\,\hat{e}_{clv,p} - \frac{\gamma}{2}\,\mathbf{s}^\top \Sigma\,\mathbf{s} $$

$$ \text{s.t.}\quad \text{exposure caps (2.4)},\ \ \text{Budget}_{day/week},\ \ s_i \le \kappa f_{kelly,i}C_{i}(\cdots)B_{eff} $$

$\mu_{clv}, \gamma$ are calibrated with Optuna, consistent with the existing utility weights (ROI / CLV / Edge / clv_consistency / CovPen). The solution is a convex QP; caps are linear constraints.

---

## 6. Interaction With Existing Layers

| Layer | Portfolio interaction |
|---|---|
| Calibration | Stake feeds Kelly via $p_{final}$ (Phase 7); better calibration -> more accurate $f_{kelly}$ and lower over-betting risk. |
| Thresholds | Only quad-gate survivors enter the portfolio; the threshold sets volume, the portfolio converts it to capital. |
| Market Trust | $T_{mkt}$ scales both $C_{combined}$ and league/market allocation; low trust -> smaller stake + lower allocation. |
| Sharp Signals | $S$ raises confidence; sharp-confirmed leagues/markets get more allocation capacity. |
| Regime Detection | In volatile regimes $\kappa\downarrow$ and $\gamma\uparrow$ (more cautious); $M_{reg}$ modulates allocation weights. |

---

## 7. Implementation Roadmap

| Step | Work package | Dependency |
|---|---|---|
| 8.1 | Bankroll Manager (high-water, ratchet $\phi$, $B_{eff}$) | PnL series |
| 8.2 | Fractional Kelly (single + simultaneous) + Phase 7 dampener integration | Phase 7 $f^{*}$ |
| 8.3 | Correlation Detection ($\Sigma$, shrinkage, clustering) | CLV/result history |
| 8.4 | Exposure + Daily/Weekly Budget constraints | 8.1 |
| 8.5 | League & Market Allocation engines | Phase 6 E/T scores |
| 8.6 | Portfolio Optimizer (CLV-augmented MV QP) | 8.2-8.5 |
| 8.7 | Portfolio Risk Score + Drawdown Protection (circuit breaker) | 8.1-8.6 |
| 8.8 | Portfolio metrics + Sharpe/CLV-Sharpe dashboard | 8.6 |
| 8.9 | Backtest + walk-forward (single vs portfolio stake) + Optuna ($\mu_{clv},\gamma,\kappa$) | all |

**Estimated duration:** ~10-12 weeks. Critical path: 8.1 -> 8.2 -> 8.6 -> 8.7. Steps 8.3 and 8.5 can run in parallel.

---

## Expected Impact

- **Higher risk-adjusted return:** mean-variance + CLV term captures the same EV at lower portfolio variance (Sharpe up).
- **Cluster-risk control:** correlation + HHI prevent single-day/single-league blowups.
- **Capital resilience:** drawdown ratchet + circuit breaker + budget discipline guarantee long-run survival (risk of ruin down).
- **Smart allocation:** capital is steered to the highest edge-quality leagues/markets within capacity limits.
