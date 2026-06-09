# MIW Faz 16 — Sharp Market Data Infrastructure & Truth Layer

> **Status:** Design — pre-implementation. **DO NOT WRITE CODE.** Pure
> architecture + mathematical specification, consistent with Faz 1–15.
> **Dependencies (consumes / sits beneath):** MIW_INGESTION_ARCHITECTURE (F3) ·
> MIW_R1_PAL_CORE · MIW_R1_2_MARKET_MEASUREMENT · MIW_SHARP_ANCHOR_CALIBRATION
> (F15) · MIW_EXECUTION_ROI_ALIGNMENT (F13) · MIW_REALWORLD_ALIGNMENT (F10) ·
> MIW_PAPER_TRADING_VALIDATION (F11) · MIW_R1_3_EDGE_DETECTION_KERNEL.

---

## 0. Problem Statement

Every higher layer — ML, CLV (F10/F15), execution (F13), edge (R1.3) — trusts an
odds stream that today is **free-API biased, incomplete, soft-skewed, missing
the true close, and inconsistent across providers**. F15 defined *how* to anchor
to the sharp market, but it has no clean sharp data to anchor *to*. Faz 16 is the
**foundational data-truth layer**: it is logically *beneath* F15 and supplies the
single canonical source of truth for odds evolution that the entire stack
consumes.

It does four things no upstream feed guarantees: (1) reconstruct a canonical
time-dependent truth probability `P_truth(t)`; (2) reconstruct the **true closing
line** even when a provider never published one; (3) rank bookmaker sharpness so
soft/free noise is down-weighted, not averaged in; (4) canonicalize every odds
format into one de-vigged probability space. Its outputs (`P_truth`, `O_truth`,
`CLV_truth`, `MSI`) replace the raw provider stream as the input to R1.2/F15.

It is a **data boundary** — it does not model, predict, or trade; it
manufactures truth from noisy multi-provider input.

---

## 1. Full System Architecture

```
   RAW PROVIDER STREAM (R1.1 PAL: OddsRecord)        FAZ 16 — SHARP MARKET DATA TRUTH LAYER
   Pinnacle · Betfair · books · free APIs   ──▶  ┌──────────────────────────────────────────────┐
                                                 │ D1 Odds Canonicalization (format → prob space) │
                                                 │ D2 Data Quality Truth Filters (stale/dup/lat.) │
                                                 │              │                                 │
                                                 │              ▼                                 │
                                                 │ D3 Market Hierarchy Model                      │
                                                 │    class ∈ {Sharp, Semi-Sharp, Soft, Free}     │
                                                 │    dynamic trust  w_i(t)                        │
                                                 │              │                                 │
                                                 │              ▼                                 │
                                                 │ D4 Sharp Truth Definition  P_truth(t)          │
                                                 │    (Pinnacle anchor + Betfair + de-vig ensemble)│
                                                 │              │                                 │
                                                 │   ┌──────────┼───────────────┬──────────────┐ │
                                                 │   ▼          ▼               ▼              ▼ │
                                                 │ D5 Closing  D6 Temporal   D7 Bias        D3→MSI│
                                                 │   Line       Truth Model    Correction         │
                                                 │   Recon.     O(t) converge  (free/soft/liq)    │
                                                 │   (O_close)                                    │
                                                 │              │                                 │
                                                 │              ▼                                 │
                                                 │ D8 Truth Outputs: P_truth · O_truth · CLV_truth · MSI │
                                                 │ D9 Integration contracts                       │
                                                 └──────────────────────────────────────────────┘
                                                                │
                                                                ▼
                       canonical truth stream → R1.2 measurement · F15 calibration · F10 CLV · F13 exec · R1.3 edge
```

| Component | Role |
|---|---|
| D1 Canonicalization | decimal/fractional/american → de-vigged probability space |
| D2 Quality filters | stale / duplicate / latency / naming / manipulation noise |
| D3 Market hierarchy | classify providers; dynamic trust `w_i(t)` → also yields MSI |
| D4 Sharp truth | `P_truth(t)` consensus, anchored on Pinnacle/Betfair |
| D5 Closing recon. | reconstruct true close from last-N snapshots (regime-aware, outlier-robust) |
| D6 Temporal model | `O(t)` with monotone convergence + temporal consistency constraints |
| D7 Bias correction | free-API bias, soft inflation, liquidity-weighted smoothing |
| D8 Outputs | `P_truth`, `O_truth`, `CLV_truth`, `MSI` |
| D9 Integration | contracts with F10/F11/F13/F15/R1.3 |

---

## 2. Mathematical Framework

Provider `i` quotes decimal odds `o_i(t)` for a selection at time `t`;
time-to-kickoff anchor `t_c` (close). Canonical de-vigged single-book prob
`p_i(t) = devig(o_i(t))` (D1/D4 ensemble).

### 2.1 (D1) Odds Canonicalization
```
decimal d                     : d
fractional a/b                : d = a/b + 1
american  m (+/−)             : d = 1 + m/100   (m>0);   d = 1 + 100/|m|   (m<0)
implied (raw)                 : q = 1/d
```
De-vig **ensemble** (averaged across methods for robustness):
```
p_mult = q_i / Σq        p_pow ∝ q_i^(1/k)        p_shin = Shin(q_i; z)
p_i = mean( p_mult, p_pow, p_shin )    (per book; methods defined in F15 §2)
```

### 2.2 (D3) Market Hierarchy & Dynamic Trust
Classes with base efficiency `η_class`: Sharp (Pinnacle, Betfair) ≈ 0.9–1.0,
Semi-Sharp ≈ 0.5–0.7, Soft ≈ 0.2–0.4, Free API ≈ 0.05–0.2. Dynamic per-provider
trust:
```
w_i(t) ∝ η_class(i) · L_i(t)^a · CA_i^b · (1/(1+λ_lat·lat_i))^c · VS_i^d
  L_i  = liquidity/depth        CA_i = closing accuracy = 1 − E|p_i(t_c) − P_truth(t_c)|
  lat_i= update latency         VS_i = vig stability (low variance of overround)
normalize: Σ_i w_i(t) = 1.    Free/soft feeds collapse toward 0 automatically.
```

### 2.3 (D4) Sharp Truth Probability
```
P_truth(t) = renorm_over_selections(  Σ_i w_i(t) · p_i(t)  )
```
Anchored: because `w` concentrates on Pinnacle/Betfair, `P_truth` ≈ sharp
consensus; soft books act only as minor noise contributors. Confidence band:
`σ_truth(t) = weighted stdev_i(p_i(t))` (cross-provider disagreement).

### 2.4 (D5) Closing Line Reconstruction
When the true close is absent, reconstruct from the last `N` trusted snapshots:
```
1. Hampel/MAD outlier rejection on the path  (drop |p − median| > k·MAD — fake spikes)
2. regime-aware smoothing:  P̂(t) = EWMA/Kalman with gain g(regime)   (R1.2 regime)
3. trust+recency weighted extrapolation to t_c:
   O_close_hat = 1 / [ Σ_n ψ_n · P̂_n / Σ_n ψ_n ],   ψ_n = w_i(t_n)·e^{−ρ(t_c − t_n)}
4. confidence = f(N, σ_truth near close, extrapolation horizon)
```
Reconstruction is flagged `RECONSTRUCTED` vs `OBSERVED` so downstream (F15 CLV)
knows the provenance.

### 2.5 (D6) Temporal Truth Model
Odds as a path `O(t)`; the efficient-market expectation is a **martingale**:
```
E[ P_truth(t+Δ) | F_t ] = P_truth(t)            (no predictable drift in an efficient market)
```
Two enforced constraints:
```
monotone convergence : Var[P_truth(t)] non-increasing as t → t_c   (uncertainty resolves)
temporal consistency : |P_truth(t+Δ) − P_truth(t)| ≤ J(Δ)          (no impossible jumps; cf. R1.2)
```
Violations (drift that is predictable, variance expansion near close, jumps >
`J`) are flagged as data defects or genuine steam — disambiguated via volume /
cross-book confirmation (D3).

### 2.6 (D8) Truth Outputs
```
P_truth(t)   : canonical time-dependent probability               (replaces p_market)
O_truth(t)   : canonical odds path = 1 / P_truth(t)
CLV_truth    : o_entry / O_close_truth − 1     (grounded on D5 reconstructed/observed close)
MSI_i        : Market Sharpness Index per provider (see §4)
MSI_market   : aggregate market sharpness/efficiency
```

---

## 3. Truth Reconstruction Methodology (D5 + D2)

1. **Canonicalize & filter** every snapshot (D1/D2) — reject stale (age > τ),
   duplicated timestamps, latency artifacts, mis-named markets (canonical map),
   and manipulation noise (single-book spikes uncorroborated by depth).
2. **Trust-weight** the surviving snapshots per provider (D3, point-in-time
   `w_i(t)`).
3. **Build `P_truth(t)`** as the weighted, de-vigged, renormalized consensus
   (D4); record `σ_truth(t)`.
4. **Reconstruct the close** (D5): Hampel outlier rejection → regime-aware
   smoothing → trust+recency-weighted extrapolation to `t_c`; tag provenance.
5. **Enforce temporal constraints** (D6); flag martingale violations / variance
   expansion / impossible jumps.
6. **Emit** `P_truth, O_truth, CLV_truth, MSI` with confidence + provenance.

No look-ahead: every `w_i(t)` and `P_truth(t)` uses only information available at
`t`; the reconstructed close at training time uses only pre-`t_c` snapshots.

---

## 4. Market Hierarchy Model & Market Sharpness Index (MSI)

**Classification** is data-driven, not hardcoded labels: a provider is *Sharp* if
its line leads others and predicts the close; *Soft/Free* if it lags and rounds.
```
MSI_i = 100 · σ( α·CA_i + β·LEAD_i − γ·vig_i − δ·round_i + ε·L_i )    ∈ [0,100]
  CA_i   = closing accuracy (predictive of truth)        LEAD_i = lead/lag (moves before others)
  vig_i  = overround        round_i = coarse-odds rounding tell      L_i = liquidity
```
`MSI` both **drives `w_i(t)`** (sharper → higher weight) and is a **reported
diagnostic** (which feeds to trust). Market-level `MSI_market` = efficiency of
the consensus (tight, low-vig, fast-converging → high). Hierarchy ordering
emerges: typically Pinnacle ≳ Betfair ≫ top books ≫ soft ≫ free.

---

## 5. Bias Correction System (D7)

| Bias | Mechanism | Correction |
|---|---|---|
| **Free-API bias** | lagged, rounded, derivative-of-sharp odds → systematic offset | estimate `b_i = E[p_i − P_truth]`; subtract, and drive `w_i(t) → ~0` via low `CA_i`/`round_i` |
| **Soft-bookmaker inflation** | recreational skew (favourite/over bias) inflates implied probs | re-anchor to `P_truth`; soft books contribute only within `σ_truth` band |
| **Liquidity distortion** | thin quotes are noisy/movable | liquidity-weighted smoothing (`L_i` in `w_i`); exclude `L_i < L_min` from the anchor |

Each correction is a **measured quantity** (offset `b_i`, weight `w_i`, MSI) that
F15's stability validator (C6) and F14's shadow checks can monitor over time.

**Critical loop guarantee:** because ML/CLV consume `P_truth`/`CLV_truth` (not raw
provider odds), **free-API bias cannot re-enter the ML feedback loop** — the
truth layer is the mandatory choke point between providers and learning.

---

## 6. Data Pipeline Design

```
PAL (R1.1) OddsRecord stream
   │  (decimal, UTC, per-book)
   ▼
D1 canonicalize ─▶ D2 quality filter ─▶ D3 trust-weight w_i(t) + MSI
   │                                          │
   ▼                                          ▼
D4 P_truth(t) + σ_truth(t)  ───────────▶  D6 temporal-consistency enforcement
   │                                          │
   ├──────────────▶ D5 closing-line reconstruction (provenance-tagged)
   │                                          │
   ▼                                          ▼
D7 bias correction ─────────────────▶  D8 TRUTH STORE
   (free/soft/liquidity)                  P_truth, O_truth, CLV_truth, MSI + confidence/provenance
                                              │
                                              ▼
        consumed by  R1.2 (measurement) · F15 (calibration) · F10 (CLV) · F13 (exec) · R1.3 (edge)
```
The **Truth Store** is the canonical, versioned, point-in-time table (warehouse,
F2/R2). Every downstream read is from the Truth Store, never from a raw provider
adapter — that is the architectural enforcement of "single source of truth".

---

## 7. Integration Rules (D9)

| Layer | Faz 16 provides | Replaces |
|---|---|---|
| **R1.2 Measurement** | `P_truth(t)`, `O_truth(t)` path | raw consensus de-vig; drift/CLV now run on truth |
| **F15 Calibration** | `P_truth` as the sharp anchor, `CLV_truth`, `MSI` for trust | F15's `P_sharp` is *populated* by F16 (F15 defined it; F16 fills it) |
| **F10 CLV Alignment** | `CLV_truth` (grounded close) | bookmaker-average CLV |
| **F13 Execution** | `O_truth` reference, liquidity/MSI per book | assumed market reference for slippage/impact |
| **R1.3 Edge Kernel** | `P_truth` market prob + `MSI`-based confidence | the de-vigged consensus it currently uses |

Hard rules: (1) **single source of truth** — downstream reads the Truth Store
only. (2) Reconstructed closes are provenance-tagged; F15 may weight
`OBSERVED` > `RECONSTRUCTED`. (3) All weights/recon point-in-time, no leakage.
(4) F16 manufactures data; it never predicts outcomes or sizes stakes.

---

## 8. Final Data Integrity Score (0–100)

```
DataIntegrity = 100 · ( w1·S_sharp + w2·S_coverage + w3·S_recon + w4·S_quality + w5·S_temporal )
  S_sharp     = real sharp feeds (Pinnacle/Betfair) ingested & weighted
  S_coverage  = provider/market/time coverage of the snapshot stream
  S_recon     = closing-line reconstruction confidence (observed vs reconstructed mix)
  S_quality   = fraction of stream passing D2 truth filters
  S_temporal  = temporal-consistency / martingale conformance
  weights (governance): w1=0.30, w2=0.20, w3=0.20, w4=0.15, w5=0.15
```

**Current honest estimate ≈ 25 / 100.** Justification:
- `S_sharp` (~0.15): **no live Pinnacle/Betfair feed ingested** — R1.1 ran on a
  recorded fixture via failover; the sharp anchor is structurally defined but
  empty.
- `S_coverage` (~0.4): PAL + R1.2 can stream multi-book snapshots, but breadth
  (leagues/markets/history) is thin.
- `S_recon` (~0.2): reconstruction methodology defined; no real close corpus to
  validate it against.
- `S_quality` (~0.4): R1.2 integrity filters exist and work on fixtures; not yet
  run at scale on real feeds.
- `S_temporal` (~0.3): temporal constraints specified; not validated on real
  paths.

This is the **root dependency** for the whole F13–F15 chain (F13≈38, F14≈30,
F15≈22, F16≈25): F16 is what *unblocks* them. It scores slightly above F15
because the data *machinery* (PAL, R1.2 filters) partly exists — what is missing
is the **real sharp feed**. Unlock sequence: **subscribe Pinnacle/Betfair (live
+ historical) → populate the Truth Store (R2 warehouse) → validate D5
reconstruction & D6 temporal constraints on real closes → F15 calibrates on
truth → F13 fits execution → F14 re-scores readiness.** With real sharp data
flowing, this score should reach 75–90, at which point the entire stack's
"profitability" becomes grounded rather than simulated.

---

### Summary
Faz 16 is the foundational truth layer that sits beneath F15 and feeds the whole
stack: it canonicalizes every odds format into one de-vigged probability space,
filters data defects, classifies providers and assigns dynamic trust `w_i(t)`
(Pinnacle/Betfair anchored), builds a time-dependent `P_truth(t)`, reconstructs
the true closing line (regime-aware, outlier-robust, provenance-tagged), enforces
a martingale/monotone-convergence temporal model, and corrects free-API / soft /
liquidity bias — emitting `P_truth`, `O_truth`, `CLV_truth`, and a Market
Sharpness Index from a single versioned Truth Store that is the only stream
downstream may read. It manufactures truth, never predictions. It is the **root
unblocker** of F13–F15; current data integrity ≈ **25/100**, gated on ingesting
real sharp (Pinnacle/Betfair) feeds into the warehouse (R2).
