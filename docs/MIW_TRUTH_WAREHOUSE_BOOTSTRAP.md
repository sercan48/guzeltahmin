# Phase 16 (Bootstrap) — Sharp Closing Odds Ingestion & Truth Warehouse

> **Status:** Design — pre-implementation. **No code. No ML retraining. No model
> changes.** Pure data infrastructure + market-truth modeling. This is the
> concrete *buildout* of the F16 concept (`MIW_SHARP_DATA_INFRASTRUCTURE.md`):
> the ingestion system + warehouse that actually **populates** the Truth Layer,
> and the realization of R2 (Historical Odds Warehouse MVP).
> **Dependencies:** MIW_SHARP_DATA_INFRASTRUCTURE (F16 concept) ·
> MIW_DATABASE_LAYER (F2) · MIW_INGESTION_ARCHITECTURE (F3) · MIW_R1_PAL_CORE ·
> MIW_R1_2_MARKET_MEASUREMENT · MIW_SHARP_ANCHOR_CALIBRATION (F15) ·
> MIW_EXECUTION_ROI_ALIGNMENT (F13) · MIW_SHADOW_LIVE_SIMULATION (F14) ·
> MIW_PAPER_TRADING_VALIDATION (F11).

---

## 1. Executive Summary

Every quantitative claim in the stack — calibration (F15), execution ROI (F13),
shadow readiness (F14), edge (R1.3) — is gated on one missing asset: a
**reliable, canonical, point-in-time store of sharp closing odds**. The F16
concept defined *how* truth should be computed; this phase builds *where it comes
from and where it lives*.

It delivers four things: (1) a **sharp closing-odds ingestion system** (Pinnacle
+ Betfair as primary/secondary anchors, via **licensed/official sources only**)
that snapshots OPEN→24h→6h→1h→CLOSE in a real-time + batch hybrid; (2) a
**Truth Store** — a single, versioned, provenance-tagged source of truth with
`OBSERVED` vs `RECONSTRUCTED` separation; (3) a **closing-line canonicalization
engine** (liquidity-weighted, de-vigged consensus); (4) a **historical backfill**
with strict per-point confidence scoring and a **no-hallucinated-data policy**.

**Hard architectural law:** downstream layers (F15 calibration, F13 execution,
F11 paper, R1.3 edge) may read **only** the Truth Store — never a raw provider
adapter. The Truth Store is the mandatory choke point that keeps free-API/soft
bias out of calibration and the ML feedback loop.

**Compliance note:** ingestion uses official Pinnacle/Betfair APIs and licensed
data vendors under their Terms of Service and applicable law. This document does
**not** design scraping-evasion or ToS circumvention; "legal proxies" is read as
*licensed/authorized access paths*, and any source whose ToS forbids automated
collection is excluded.

---

## 2. System Architecture

```
        AUTHORIZED SOURCES                       PHASE-16 TRUTH WAREHOUSE PIPELINE
  ┌───────────────────────────┐     ┌────────────────────────────────────────────────────────────┐
  │ Pinnacle API (anchor #1)   │     │  A. INGESTION (real-time + batch hybrid)                     │
  │ Betfair Exchange (anchor#2)│────▶│     scheduler: OPEN→24h→6h→1h→CLOSE  ·  rate/quota guard      │
  │ licensed odds vendors      │     │     PAL adapters (R1.1) → normalized OddsRecord               │
  │ (Odds API etc., per ToS)   │     │            │                                                 │
  └───────────────────────────┘     │            ▼                                                 │
                                     │  B. STREAM (hot path)        C. BATCH (cold path / backfill) │
                                     │     low-latency snapshots       historical replay + recon.   │
                                     │            │                          │                      │
                                     │            ▼                          ▼                      │
                                     │  D. CANONICALIZATION ENGINE (de-vig ensemble + liq-weighted) │
                                     │            │                                                 │
                                     │            ▼                                                 │
                                     │  E. CLOSING-LINE DEFINITION (true close: observed or recon.) │
                                     │            │                                                 │
                                     │   ┌────────┼─────────────┬───────────────┐                  │
                                     │   ▼        ▼             ▼               ▼                  │
                                     │  F. DQ &  G. Market     H. Provenance   I. Conflict          │
                                     │  Integrity  Drift         Tagging         Resolution         │
                                     │            │                                                 │
                                     │            ▼                                                 │
                                     │  ===========  TRUTH STORE (single source of truth)  ======== │
                                     └────────────────────────────────────────────────────────────┘
                                                        │  (read-only, point-in-time)
                                                        ▼
                       F15 calibration · F13 execution · F14 shadow · F11 paper · R1.3 edge
```

**Lambda-style hybrid:** the **hot path** captures live snapshots at the schedule
points with minimal latency; the **cold path** does heavy historical backfill,
reconstruction, and re-canonicalization. Both write to the same Truth Store with
provenance; the cold path may *supersede* hot rows (versioned), never silently
overwrite.

---

## 3. Truth Store Schema

Two layers: an immutable **raw snapshot ledger** (audit) and the derived
**canonical truth table** (consumed). All timestamps UTC, tz-aware,
point-in-time.

### 3.1 `odds_snapshot_raw` (immutable append-only ledger)
| Field | Type | Notes |
|---|---|---|
| snapshot_id | uuid | PK |
| match_id | str | canonical fixture id |
| provider | str | pinnacle / betfair / vendor_x |
| provider_class | enum | SHARP / SEMI_SHARP / SOFT / FREE |
| market | str | 1X2 / O\_U / AH / … (canonical) |
| selection | str | HOME / OVER_2.5 / … (canonical) |
| odds_decimal | float | normalized to decimal (>1.0) |
| snapshot_type | enum | OPEN / T24H / T6H / T1H / CLOSE / LIVE |
| collected_at | ts | when WE captured it (latency anchor) |
| provider_ts | ts\|null | provider's own update time if available |
| liquidity | float\|null | matched volume / depth (Betfair) |
| ingest_path | enum | HOT / COLD |
| source_quota_ctx | json | rate/quota state at capture (audit) |

### 3.2 `truth_odds` (canonical, versioned, consumed downstream)
| Field | Type | Notes |
|---|---|---|
| truth_id | uuid | PK |
| match_id, market, selection | str | canonical keys |
| snapshot_type | enum | OPEN…CLOSE |
| as_of | ts | point-in-time validity |
| p_truth | float | de-vigged canonical probability (0–1) |
| o_truth | float | 1 / p_truth |
| sigma_truth | float | cross-provider disagreement (confidence band) |
| provenance | enum | **OBSERVED** / **RECONSTRUCTED** |
| confidence | float | 0–1 (1 for clean OBSERVED; scored for RECONSTRUCTED) |
| contributing_providers | json | {provider: weight w_i} used in the consensus |
| devig_method | enum | SHIN / POWER / LOGIT / ENSEMBLE |
| msi_market | float | market sharpness index at as_of |
| version | int | supersession version (cold path may bump) |
| superseded_by | uuid\|null | lineage |

### 3.3 `closing_truth` (the headline asset)
| Field | Type | Notes |
|---|---|---|
| match_id, market, selection | str | keys |
| o_close_truth | float | canonical true closing odds |
| p_close_truth | float | 1 / o_close_truth |
| provenance | enum | OBSERVED / RECONSTRUCTED |
| confidence | float | 0–1 |
| recon_method | enum\|null | which §6 method, if reconstructed |
| anchor_mix | json | {pinnacle:.., betfair:.., …} weights |

`OBSERVED` and `RECONSTRUCTED` are **never blended without tagging**; downstream
(F15) may filter or down-weight `RECONSTRUCTED < OBSERVED`.

---

## 4. Closing Line Definition Engine

**"True closing line"** = the liquidity- and sharpness-weighted, de-vigged
consensus probability at the last point before kickoff, dominated by the sharp
anchors.
```
For each provider i at close:  p_i = devig_ensemble(o_i)        (Shin / power / logit, per F15 §2)
Trust weight (F16 concept):    w_i ∝ η_class(i)·L_i^a·CA_i^b·(1/(1+λ·lat_i))^c·VS_i^d
Canonical close:               p_close_truth = renorm( Σ_i w_i · p_i ),   o_close_truth = 1/p_close_truth
Confidence band:               sigma = weighted stdev_i(p_i)
```
- **Pinnacle** is the primary anchor (highest `η`, deep, sharp); **Betfair**
  exchange is the secondary anchor (true traded prices, `L_i` = matched volume).
- Soft/free providers enter only as minor contributors inside the `sigma` band
  and never override the anchors.
- De-vig **ensemble** (mean of Shin/power/logit) reduces single-method bias;
  `devig_method = ENSEMBLE` recorded.

---

## 5. Ingestion Pipeline Design

**Snapshot schedule** (per fixture, aligned to R1.2 horizons): `OPEN` (on
listing), `T-24h`, `T-6h`, `T-1h`, `CLOSE` (last quote before kickoff), plus
opportunistic `LIVE` if available. The scheduler is **kickoff-relative** and
quota-aware (Pinnacle free tier caps, Betfair throughput).

```
hot path  : event-/timer-driven; capture at each schedule point with minimal lag;
            write odds_snapshot_raw(ingest_path=HOT); compute truth_odds incrementally.
cold path : nightly/periodic; replays the ledger, runs §6 reconstruction + §4 canonicalization,
            backfills gaps, recomputes MSI/drift, supersedes truth rows (versioned).
guards    : rate-limit + circuit breaker (R1.1), retry/backoff, quota budgeting across the day
            so the CLOSE snapshot is never starved by earlier calls.
```
The **CLOSE capture is privileged**: the scheduler reserves quota and tightens
cadence in the final window so the most valuable snapshot is the most reliable.

---

## 6. Backfill Strategy (strict no-hallucination)

Goal: reconstruct missing historical closes **without inventing data**.

```
Inputs allowed : only real pre-close snapshots actually observed for that match.
Method ladder (best available wins, provenance + confidence recorded):
  M0 OBSERVED        : a real CLOSE snapshot exists                  → confidence 1.0
  M1 NEAR-CLOSE      : last observed snapshot within ε of kickoff    → high confidence
  M2 TREND EXTRAPOL. : trust+recency-weighted extrapolation of last N snapshots to t_c
                       (after Hampel outlier rejection)              → medium confidence
  M3 ANCHOR TRANSFER : map a sharp anchor's close via a calibrated, *historically fit*
                       offset to a book that has data                → lower confidence
Confidence score:  conf = g(N_snapshots, extrapolation_horizon, sigma_near_close, anchor_agreement)
```
**No-hallucinated-data policy (hard constraint):**
- If no real pre-close snapshot exists for a match, the close is left **NULL**
  with `provenance = NONE` — it is *never* fabricated.
- Reconstructed points are always tagged `RECONSTRUCTED` + `confidence < 1` and
  carry `recon_method`.
- A global config `min_confidence_for_calibration` lets F15 exclude low-confidence
  reconstructions entirely.
- Reconstruction may only *interpolate/extrapolate within observed evidence*; it
  may never synthesize a price for a market that was never observed.

---

## 7. Data Quality & Integrity System

| Check | Definition | Action |
|---|---|---|
| **Duplicate closing** | same (match, market, selection) CLOSE from a provider repeated | dedup, keep highest-confidence/last; log |
| **Stale odds** | `collected_at − provider_ts > τ_stale`, or no movement across windows on a live market | flag; down-weight `w_i`; exclude from anchor if severe |
| **Bookmaker manipulation noise** | single-book spike uncorroborated by depth/other books | Hampel/MAD rejection; require cross-book confirmation before trusting |
| **API latency bias** | systematic `collected_at` lag distorts which line is "the close" | latency-correct: align to `provider_ts` when present; estimate per-provider lag offset and compensate |
| **Naming inconsistency** | provider market/selection labels differ | canonical mapping (R1.1) before storage |
| **Impossible jump** | `|Δp|` between snapshots beyond `J(Δt)` | flag as defect or steam; disambiguate via volume/cross-book |

All checks run **before** a row becomes part of `truth_odds`; failures are quarantined
in the raw ledger with a reason code, not propagated.

---

## 8. Market Truth Drift System

Sharp-market efficiency is **not constant** across leagues or time:
```
MSI_league(t)  : rolling Market Sharpness Index per league (EPL high; Süper Lig / lower divisions lower)
drift signal   : ΔMSI over rolling windows → detects a market getting sharper/softer (e.g. more liquidity)
liquidity regime: classify each league/market into {deep, medium, thin} from Betfair matched volume + book count
sharpness evolution: track CA_i (closing accuracy) per provider per league over time
```
Consequences: trust weights `w_i(t)` and the anchor mix become **league- and
regime-conditioned** — e.g. Betfair may out-anchor Pinnacle in a market where its
liquidity is deeper; a thin league leans harder on Pinnacle and widens
`sigma_truth`. Drift events feed F15's regime-aware stability (C6) and F14's
shadow stress.

---

## 9. Integration with the MIW Stack (F13–F15, F11)

| Layer | Reads from Truth Store | Contract |
|---|---|---|
| **F15 Sharp Calibration** | `p_truth`, `closing_truth`, `MSI`, confidence | `P_sharp` is *populated* by this layer; F15 may require `provenance=OBSERVED` or `confidence ≥ min` |
| **F13 Execution ROI** | `o_truth` path, liquidity, `MSI` per book | market reference for slippage/impact; `CLV_realized` uses `closing_truth` |
| **F14 Shadow Simulation** | versioned point-in-time `truth_odds` | deterministic replay source; drift/regime feed stress scenarios |
| **F11 Paper Trading** | `o_truth`, `closing_truth` | settles paper CLV against the canonical close, not a raw feed |
| **R1.3 Edge Kernel** | `p_truth` as market prob, `MSI` | replaces raw consensus de-vig |

**Enforced laws:** (1) **calibration consumes the Truth Store only** — a raw
provider read downstream is an architecture violation (lint/contract test). (2)
Provenance and confidence travel with every value. (3) Point-in-time: a read
`as_of t` returns only what was known at `t` (no leakage). (4) This layer never
predicts or sizes — pure data.

---

## 10. Risks & Failure Modes

| Risk | Symptom | Mitigation |
|---|---|---|
| **Sparse sharp coverage** | many matches lack Pinnacle/Betfair pre-close data | start with high-coverage leagues (EPL/top-5); widen as data grows; NULL-not-fabricate |
| **Reconstruction over-trust** | low-confidence recon treated as truth | hard confidence gating; F15 excludes below threshold; OBSERVED preferred |
| **Latency mislabels the close** | a late-but-stale quote stored as CLOSE | latency offset correction; privilege `provider_ts`; tighten close-window cadence |
| **Quota starvation of CLOSE** | early calls exhaust quota, close snapshot missed | reserved quota budget for the final window |
| **Anchor outage** | Pinnacle/Betfair down at close | fall back to next-sharpest with widened `sigma`; tag lower confidence |
| **Manipulation / fake steam** | single-book spike moves naive consensus | cross-book confirmation + Hampel rejection before anchoring |
| **Silent overwrite** | cold path clobbers good hot rows | versioned supersession with lineage; never destructive |
| **ToS/compliance breach** | unauthorized source ingested | source allow-list gated on license/ToS; excluded otherwise |
| **Survivorship in backfill** | only "interesting" matches reconstructed | systematic, match-complete backfill; record coverage ratio |

---

## 11. Final "System Unlock Score" (0–100)

This phase is the **keystone** — it is the single dependency gating F13–F16. The
unlock score measures how much of the downstream stack this layer makes *real*.
```
UnlockScore = 100 · ( w1·U_anchor + w2·U_close + w3·U_coverage + w4·U_quality + w5·U_integration )
  U_anchor      = real Pinnacle/Betfair feeds live + historical, under license
  U_close       = closing_truth populated (OBSERVED-heavy) across target leagues
  U_coverage    = breadth (leagues × markets × seasons) in the Truth Store
  U_quality     = DQ pass-rate + latency-corrected + provenance-complete
  U_integration = downstream reads routed exclusively through the Truth Store
  weights: w1=0.30, w2=0.25, w3=0.20, w4=0.15, w5=0.10
```

**Current (pre-build) ≈ 18 / 100** — the design exists; the Truth Store is empty
of real sharp data (R1.1 ran on a recorded fixture; no licensed Pinnacle/Betfair
feed is wired). **Projected post-bootstrap ≈ 80–90** once anchors are licensed,
the close schedule runs, and OBSERVED closes accumulate.

**Cascade effect** — completing this single layer re-scores the whole chain (the
honest current estimates from F13–F16 all share this root dependency):
```
            current → projected after this bootstrap
  F16 data integrity   25 → 80
  F15 calibration      22 → 75
  F13 execution realism 38 → 70   (params now fit on real fills + closes)
  F14 live readiness   30 → 70
```
This is the highest-leverage build in the program: it converts the entire
stack from **"simulated profitability"** to a **grounded, real-market** system.

---

### Summary
Phase 16 (bootstrap) builds the asset everything else waits on: a licensed,
ToS-compliant ingestion of Pinnacle/Betfair closing odds on an
OPEN→24h→6h→1h→CLOSE schedule (real-time + batch hybrid), feeding a versioned,
provenance-tagged **Truth Store** with strict `OBSERVED` vs `RECONSTRUCTED`
separation, a liquidity-weighted de-vigged **closing-line definition**, a
**no-hallucination backfill** with per-point confidence, a DQ/integrity gate, and
a league/regime-aware market-drift system. Downstream (F15/F13/F14/F11/R1.3) reads
the Truth Store **only**. It writes no model and predicts nothing — it
manufactures ground truth. Current unlock ≈ **18/100**; building it lifts the
whole F13–F16 chain into the 70–90 range and turns simulated profitability into
real-market profitability.
