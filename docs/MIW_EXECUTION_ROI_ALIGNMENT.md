# MIW Faz 13 — Realistic Execution & ROI Alignment Engine

> **Status:** Design — pre-implementation. **DO NOT WRITE CODE.** This document
> is pure architecture + mathematical specification, consistent with the Faz 1–12
> design docs.
> **Dependencies:** MIW_PORTFOLIO_ENGINE (Faz 8) · MIW_HISTORICAL_ODDS_WAREHOUSE
> (Faz 9) · MIW_REALWORLD_ALIGNMENT (Faz 10) · MIW_PAPER_TRADING_VALIDATION
> (Faz 11) · MIW_GOVERNANCE_DEPLOYMENT (Faz 12) · MIW_R1_2_MARKET_MEASUREMENT ·
> MIW_R1_3_EDGE_DETECTION_KERNEL.

---

## 0. Problem Statement

Every layer upstream measures *theoretical* value: the edge kernel (R1.3)
produces `EQS`/tiers from `p_model` vs `p_market`; the CLV layer (Faz 10)
measures closing-line value; the portfolio engine (Faz 8) allocates against
expected value. **None of them model whether that value survives execution.**

A 9% edge on a thin Tier-3 market that fills 20% of the time at 3% slippage is
worth a fraction of a 4% edge on a deep Tier-1 exchange that fills fully. Faz 13
is the discount layer that turns *theoretical* ROI into *realistically
executable* ROI, and corrects historical backtests for the same effects.

**Inputs:** `p_model`, edge score / EQS / tier (R1.3), market signals (drift,
liquidity, sharp_proxy from R1.2), CLV signal (Faz 10).
**Outputs:** executable ROI, slippage-adjusted & liquidity-adjusted performance,
stake fill probability, and a true-vs-theoretical ROI decomposition.

---

## 1. Full Architecture

```
        UPSTREAM (unchanged)                         FAZ 13 — EXECUTION & ROI ALIGNMENT ENGINE
  ┌──────────────────────────┐        ┌──────────────────────────────────────────────────────────────┐
  │ R1.3 Edge Kernel          │ edge,  │  E1 Market Microstructure Model                                │
  │  EQS, tier, sharp_adj_edge│──────▶│     liquidity tiers · sharp/public split · timing-decay        │
  │ R1.2 Measurement          │ drift, │           │                                                    │
  │  drift, sharp_proxy, eff. │──────▶│           ▼                                                    │
  │ Faz 10 CLV Alignment      │ CLV    │  E2 Execution Slippage Model ── P(fill) curve                  │
  │ Faz 8 Portfolio           │ stake  │           │                                                    │
  └──────────────────────────┘  req.  │           ▼                                                    │
                                        │  E3 Market Impact Model (self-impact at scale)                 │
                                        │           │                                                    │
                                        │           ▼                                                    │
                                        │  E4 Stake Realism Layer (frac/capped Kelly, risk-parity,       │
                                        │     constraint-aware sizing)                                   │
                                        │           │                                                    │
                                        │           ▼                                                    │
                                        │  E5 Edge Realization Function  P(edge realized | constraints)  │
                                        │           │                                                    │
                                        │           ▼                                                    │
                                        │  E6 True ROI Decomposition  (theo / exec / liq / CLV-realized) │
                                        │           │                                                    │
                                        │           ├──────────────▶ E7 Backtest Correction Engine       │
                                        │           ▼                                                    │
                                        │  E8 Realistic Performance Scores: RROS · EEI · MRR             │
                                        │  E9 Failure-Mode Detectors (CLV overfit, fake sharp, traps)    │
                                        └──────────────────────────────────────────────────────────────┘
                                                    │
                                                    ▼
                          Realistic, executable signal → Portfolio (Faz 8) / Paper Trading (Faz 11)
```

**Engine boundary principle:** Faz 13 never changes a model probability, a
calibration, an edge, or a CLV value. It only **discounts** them by realistic
execution physics and **measures** the discount. Upstream layers stay
theoretical; this engine produces the executable view.

| Component | Role |
|---|---|
| E1 Microstructure | Classify market into liquidity tier, sharp/public regime, timing window |
| E2 Slippage + P(fill) | Map (liquidity, speed, stake, market type) → expected slippage & fill probability |
| E3 Market Impact | Self-impact of own/system order on the line at scale |
| E4 Stake Realism | Constraint-aware sizing (fractional/capped Kelly, risk-parity) |
| E5 Edge Realization | P(edge survives execution); edge→profit conversion rate |
| E6 ROI Decomposition | theoretical → execution → liquidity → CLV-realized |
| E7 Backtest Correction | Strip inflated backtest ROI using E1–E6 |
| E8 Performance Scores | RROS, EEI, MRR |
| E9 Failure Modes | Detect CLV-overfit, fake sharp, liquidity traps, low-fill/high-edge |

---

## 2. Mathematical Definitions

Notation. Side `s ∈ {back, lay}`; quoted decimal odds `o_q`; filled odds `o_f`;
closing odds `o_c`; model prob `p` (= `p_model`); market fair prob `p_mkt`;
stake (attempted) `q`; bankroll `B`; market depth / available volume at the line
`D`; stake-to-depth ratio `ρ = q/D`; line speed `v` = `|prob_velocity|` from
R1.2; time-to-event `Δt`. Edge (headline, post-cascade) `e = sharp_adjusted_edge`
from R1.3.

### 2.1 (E2) Execution Slippage Model

Slippage is the adverse fractional difference between intended and filled odds:
```
slip = (o_q − o_f) / o_q                 (back side; ≥0 is adverse — got worse odds)
```
Expected slippage as a function of the four required drivers:
```
E[slip] = κ_size · ρ^γ            (stake size vs depth, convex γ>1: large orders eat the book)
        + κ_speed · v · Δt_fill   (odds movement speed during the fill window)
        + κ_mkt(m)                (market-type base spread: 1X2 < O/U < props/AH)
γ ≈ 1.5,  κ_size, κ_speed, κ_mkt(m) calibrated from paper-trading fills (Faz 11).
```
**Fill probability curve.** Probability the attempted stake is matched at/inside
the limit before the line moves away or the event starts:
```
P(fill | q) = 1 / (1 + (q / q_50)^η)             (decreasing in stake; q_50 = 50%-fill stake)
q_50 = q_50(D, v, Δt, m)  ∝  D · g(Δt) / (1 + κ_v·v)
```
Expected **filled fraction** (partial fills):
```
φ = E[ min(q_matched, q) / q ]  ∈ [0,1]
```
A clean tractable form: `φ ≈ P(fill | q)` when fills are all-or-nothing, else
`φ = ∫ depth-consumption profile`. `q_50` shrinks as depth falls, line speed
rises, or the event nears.

### 2.2 (E3) Market Impact Model

Own order moves the line (Kyle-λ / square-root impact):
```
Δp_self = λ_impact · ρ^δ          (price impact in prob space; δ∈[0.5,1])
o_eff   = 1 / (p_mkt + Δp_self)    (you push prob up → odds down → against you)
```
Temporary vs permanent: `Δp_self = Δp_perm + Δp_temp`, `Δp_temp` decays with
half-life `h_imp` after the order; `Δp_perm` persists (information leakage).

**System self-impact detection (scale).** Aggregate system stake `Q_sys` across
correlated selections can move the *consensus*. Detect by comparing observed
post-order drift to a market baseline:
```
self_impact_signal = Δp_observed − Δp_baseline
flag if  self_impact_signal  >  τ_imp  AND  attributable to own volume share (Q_sys/D)
```
This guards against the system trading against its own footprint at scale.

### 2.3 (E5) Edge Realization Function

Breakeven odds for the model: `o_be = 1/p`. The edge is realized only if the
fill keeps it positive AND the order fills:
```
P(edge realized | constraints) = P(fill) · P(o_f ≥ o_be | filled) · P(no adverse selection)
```
where adverse selection = the fills you get are disproportionately the ones the
line was about to move against (informed counterparties).
**Edge→profit conversion rate** (how much theoretical edge survives):
```
CR = clip( (p · o_f · φ − 1) / (p · o_q − 1) , 0, 1 )       (realized edge / theoretical edge)
```

### 2.4 (E4) Stake Realism Layer

Full-Kelly fraction (back side): `f* = e / (o_q − 1)`. Three sizing regimes,
all constraint-aware:
```
fractional Kelly :  f_frac = c · f*                          (c∈(0,1], e.g. 0.25)
capped Kelly     :  f_cap  = min(f_frac, f_max)              (hard exposure cap)
risk-parity      :  f_rp,i = (σ_target / σ_i) / Σ_j(σ_target/σ_j)   (equal risk contribution)
```
Final realistic stake respects liquidity and fill:
```
q* = B · min( f_chosen , ρ_max · D / B )      s.t.  P(fill | q*) ≥ p_fill_min
```
Sizing logic lives in Faz 8; Faz 13 only supplies the **execution constraints**
(`ρ_max`, `p_fill_min`, `o_eff`) the sizer must obey.

### 2.5 (E6) True ROI Decomposition

Four nested ROI levels (per unit of intended exposure), each stripping one layer
of optimism:
```
ROI_theoretical = p · o_q − 1                                  (ideal: quoted odds, full fill, no impact)
ROI_execution   = φ · ( p · o_f − 1 )                          (apply slippage o_f and fill fraction φ)
ROI_liquidity   = φ · ( p · o_eff − 1 )                        (further apply self-impact o_eff)
ROI_CLV_realized = κ_clv · CLV_realized,   CLV_realized = E[ o_f / o_c − 1 ]   (return attributable to beating the close)
```
**Decomposition identity** (theoretical = realistic + drags):
```
ROI_theoretical = ROI_liquidity
                + slippage_drag + fill_drag + impact_drag
slippage_drag = p·(o_q − o_f)
fill_drag     = (1 − φ)·(p·o_f − 1)
impact_drag   = φ·p·(o_f − o_eff)
```
`ROI_CLV_realized` is the orthogonal cross-check: long-run executable ROI should
track `κ_clv · CLV_realized`. A persistent gap `ROI_liquidity − ROI_CLV_realized`
signals either CLV-overfit (E9) or mis-calibrated `κ_clv`.

### 2.6 (E1) Market Microstructure Model

**Liquidity tiers** (depth-ordered): `T1` deep exchanges/top books, `T2` standard
books, `T3` soft/thin/props. Each tier has `(D_tier, q_max_tier, κ_mkt_tier)`;
slippage and `q_50` scale with `D_tier`.
**Sharp vs public segmentation:** classify a move by volume profile + line
response: sharp = low volume, large line response, leads other books; public =
high volume on favourites/overs, lagging line. Feeds R1.2's `sharp_proxy` and
weights `κ` in fill/impact.
**Timing advantage decay curve:** value of acting early decays as the market
incorporates information toward the close:
```
A(Δt) = A_0 · exp(−ζ · (Δt_open − Δt))          (earlier = higher potential CLV, higher variance)
```
Trade-off: early entry maximizes expected CLV but raises uncertainty and the
chance the line moves against you before settlement.

---

## 3. Execution Flow

```
1. Edge kernel emits (selection, p_model, e=sharp_adjusted_edge, EQS, tier)        [R1.3]
2. E1 classifies market: liquidity tier, sharp/public regime, timing window Δt
3. E2 computes E[slip] → o_f, and P(fill|q) → φ
4. E3 computes self-impact Δp_self → o_eff   (+ system self-impact flag)
5. E5 computes P(edge realized | constraints) and conversion rate CR
6. E4 supplies execution constraints to the portfolio sizer → realistic stake q*
7. E6 decomposes ROI: theoretical → execution → liquidity → CLV-realized + drags
8. E9 runs failure-mode detectors; if any trip → downgrade / veto the signal
9. E8 emits RROS, EEI, MRR for the bet and (aggregated) for the system
10. Executable signal forwarded to Portfolio (Faz 8) / logged in Paper Trading (Faz 11)
```

The **gate**: a signal proceeds only if
`P(edge realized) ≥ p_realize_min` AND `P(fill|q*) ≥ p_fill_min` AND no E9 flag.
A high-EQS Tier-S edge on a market that fails the fill gate is **demoted** — EQS
is necessary but not sufficient for execution.

---

## 4. ROI Decomposition Framework

| Level | Formula | Strips | Reads as |
|---|---|---|---|
| Theoretical | `p·o_q − 1` | — | "paper" edge the kernel saw |
| Execution-adjusted | `φ·(p·o_f − 1)` | slippage + fill | what you'd average after real fills |
| Liquidity-adjusted | `φ·(p·o_eff − 1)` | + self-impact | what survives at *your scale* |
| CLV-realized | `κ_clv·E[o_f/o_c − 1]` | model-noise | the durable, close-anchored component |

**Waterfall** (illustrative magnitudes, to be calibrated from Faz 11 fills):
```
ROI_theoretical        +6.0%
  − slippage_drag      −1.4%
  − fill_drag          −1.1%   (unfilled high-edge portion)
  − impact_drag        −0.6%
= ROI_liquidity        +2.9%   ← the number that should drive sizing
CLV_realized cross-check: +3.1%  → consistent (gap 0.2% within tolerance)
```
The headline lesson of Faz 13: **the sizing/governance layers must consume
`ROI_liquidity`, not `ROI_theoretical`.** Reporting `ROI_theoretical` as
"performance" is the single largest source of strategy disappointment.

---

## 5. Risk Analysis & Failure Modes (E9)

| Failure mode | Definition | Detector | Response |
|---|---|---|---|
| **CLV overfitting** | Chasing CLV that does not convert to ROI | `ROI_liquidity ≪ κ_clv·CLV_realized` sustained; MRR < 0.5 with high measured CLV | Down-weight CLV in selection; recalibrate `κ_clv` |
| **Fake sharp signals** | Soft-book-led move misread as sharp | No cross-book confirmation; sharp move not led by T1 depth; volume profile public | Require T1 confirmation before trusting `sharp_proxy` |
| **Liquidity traps** | High edge on thin market, unfillable at scale | `e` high but `D` low, `P(fill|q*)` low, `ρ` forced high | Cap stake to `q_50`-region; demote tier |
| **Low-fill / high-edge** | Edge concentrated where fills are rare | `e·EQS` high but `φ` low across the segment | Exclude from RROS; flag as non-executable alpha |
| **Adverse selection** | The fills you get are the ones about to move against you | `o_f` systematically < `o_c` for filled bets only | Tighten limits; widen timing window away from close |
| **Self-impact at scale** | System volume moves the consensus | E3 `self_impact_signal > τ_imp` | Throttle correlated stake; stagger entries |

Structural risks: (a) execution params are only as good as the paper-trading
calibration set (Faz 11) — uncalibrated params ⇒ over-optimistic discounts;
(b) regime shift — liquidity/slippage curves drift across seasons/leagues and
must be re-estimated; (c) closing-line availability — `CLV_realized` needs real
closes (R2/R3), absent which the cross-check is directional only.

---

## 6. Performance Scores (E8)

```
Execution Efficiency Index   EEI = ROI_liquidity / ROI_theoretical              ∈ [0,1]   (edge→reality conversion)
Market Realization Ratio     MRR = CLV_realized   / CLV_measured                ∈ [0,1]   (how much measured CLV is captured)
Real ROI Score               RROS = 100 · σ( a·ROI_liquidity_ann + b·EEI + c·MRR − d·DD_real )
   where ROI_liquidity_ann = annualized liquidity-adjusted ROI,
         DD_real           = realistic max drawdown (post-execution),
         σ                 = squashing to [0,100], weights a,b,c,d governance-set.
```
Interpretation: `EEI≈1, MRR≈1` ⇒ the theoretical edge is real and capturable;
low `EEI` with high theoretical ROI is the classic backtest mirage that E7
corrects.

---

## 7. Backtest Correction Engine (E7)

Replay historical signals through E1–E6 instead of assuming mid-price full fills:
```
for each historical bet:
   reconstruct point-in-time depth/speed/tier (no look-ahead)
   drop bet if P(fill|q_hist) < p_fill_min            (it would not have existed)
   replace o_q → o_f (slippage), apply φ, apply o_eff (impact)
   cap q at historical liquidity
ROI_corrected = Σ executable returns / Σ deployed capital
Inflation_factor = ROI_naive / ROI_corrected          (report alongside every backtest)
```
Governance rule (Faz 12): **no strategy is promoted on `ROI_naive`;** promotion
requires `ROI_corrected`, `EEI`, and `Inflation_factor` within thresholds.

---

## 8. Integration Rules

| Layer | Faz 13 reads | Faz 13 writes back |
|---|---|---|
| **Edge Kernel (R1.3)** | `p_model`, `sharp_adjusted_edge`, `EQS`, tier, `f_drift/f_sharp` | `executable_edge = e·CR·P(edge realized)`; tier demotion on fill/trap gates |
| **CLV Engine (Faz 10)** | `CLV_measured`, expected vs realized CLV | `MRR`, recalibrated `κ_clv`, CLV-overfit flag |
| **Paper Trading (Faz 11)** | real fills, slippage, fill rates | **calibration ground-truth** for `κ_size, κ_speed, λ_impact, q_50` |
| **Portfolio Engine (Faz 8)** | desired exposure, correlations | execution constraints (`o_eff`, `ρ_max`, `p_fill_min`), `ROI_liquidity` as the EV input |

Hard rules: (1) Portfolio sizes on `ROI_liquidity`, never `ROI_theoretical`.
(2) Paper Trading is the *only* source of truth for execution parameters — they
are calibrated, not assumed. (3) The edge kernel's tier is an input, not a
verdict; the fill/impact gates can veto it. (4) No parameter in Faz 13 is fit on
the same data used to validate it (walk-forward, per Faz 11).

---

## 9. Final System Realism Score (0–100)

A meta-score of how *realistic* the system's reported performance currently is,
given which execution components are calibrated vs assumed:
```
Realism = 100 · ( w1·C_exec + w2·C_clv + w3·C_data + w4·C_validation )
  C_exec       = fraction of execution params calibrated from real/paper fills   (E2,E3)
  C_clv        = closing-line availability & CLV-realized coverage               (needs R2/R3)
  C_data       = liquidity/depth data coverage across tiers
  C_validation = backtest-correction applied + walk-forward in place
  weights (governance): w1=0.35, w2=0.25, w3=0.20, w4=0.20
```

**Current honest estimate ≈ 38 / 100.** Justification:
- `C_exec` low (~0.3): slippage/impact/fill **math is specified but not yet
  calibrated** from real fills — Faz 11 paper trading must supply the constants.
- `C_clv` low (~0.3): `CLV_realized` needs the real **closing line** (R2/R3);
  today only the provisional close from R1.2 exists.
- `C_data` moderate (~0.4): we have odds snapshots and book confidence, but no
  true depth/volume feed for liquidity tiers yet.
- `C_validation` moderate (~0.5): backtest-correction framework is defined and
  walk-forward is mandated, but not yet wired into CI.

The score is intentionally modest: the *theoretical* stack (R1.2/R1.3, Faz 6–12)
is strong, but **realism is gated on execution calibration and real closing
lines**, which are the next concrete dependencies (R2/R3 + Faz 11 fills). When
those land, the same formula should rise toward 70–85.

---

### Summary
Faz 13 specifies the discount layer that converts theoretical edge/CLV into
**executable ROI**: a slippage + fill-probability model, a self-impact model, an
edge-realization function, a four-level ROI decomposition (theoretical →
execution → liquidity → CLV-realized), a backtest-correction engine that kills
inflated paper performance, and three realism scores (RROS, EEI, MRR). It
integrates as a *measurement/discount* boundary — it never alters models,
calibration, edges, or CLV; it tells the portfolio and governance layers what is
actually capturable. Implementation is blocked on two real dependencies: real
closing lines (R2/R3) and execution-parameter calibration from paper-trading
fills (Faz 11). Current system realism ≈ **38/100**, rising as those land.
