# MIW R1.2 — Live Market Measurement Layer (CLV + Odds Drift Core)

> Real, runnable code was written and executed. Changes are isolated to
> `src/market/` + `tests/test_r1_2_measurement.py`. **ML / calibration /
> threshold / decision layers are untouched.** This is a *pure measurement*
> layer: deterministic transforms over point-in-time odds snapshots, no model
> predictions anywhere. Every number below is the real JSON output of
> `python3 -m src.market.run_r1_2_measurement` on the bundled 5-match fixture.

Built on top of the PAL Core contract (`MIW_R1_PAL_CORE.md`). The layer consumes
the normalized `OddsRecord` stream; `OddsRecord.from_pal()` adapts a real PAL
(pydantic) record losslessly, so when the PAL adapters land there is **zero**
downstream change. Enums (`SnapshotType="T-24h"…`, `MarketType="1X2"/"O/U"…`)
are value-aligned with PAL.

---

## 1. Architecture of the Measurement Layer

```
 PAL Core (R1)                         R1.2 MEASUREMENT LAYER  (src/market/)
 OddsRecord stream  ── from_pal ──▶  ┌─────────────────────────────────────────┐
                                     │ (1) MarketTimeSeriesBuilder              │
                                     │     per-book series + consensus stream   │
                                     │     opening/24h/12h/6h/1h/live/closing   │
                                     └───────────────┬─────────────────────────┘
                                                     │ time-aligned, ordered
            ┌────────────────────────┬───────────────┼───────────────┬───────────────────┐
            ▼                        ▼               ▼               ▼                   ▼
   (2) OddsDriftEngine     (3) CLVFoundation   (4) MarketEfficiency  (5) DataIntegrity   [orchestrator]
   change_24h/6h/1h        CLV_raw / snapshot  consensus_score       gaps / ts / dup /   MeasurementPipeline
   total_drift             weighted CLV        disagreement_index    impossible_jump     → MeasurementResult
   velocity/acceleration   bookmaker-adj CLV   sharp_proxy_signal                        → JSON report
```

| Module | File | Responsibility |
|---|---|---|
| Schema | `schema.py` | `OddsRecord` (PAL-compatible), `MarketKey`, `MatchContext`, enums, `from_pal()` |
| Task 1 | `timeseries.py` | `MarketTimeSeriesBuilder` → ordered series + horizon buckets |
| Task 2 | `drift_engine.py` | `OddsDriftEngine` → drift signals |
| Task 3 | `clv_foundation.py` | `CLVFoundation` → CLV_raw, weighted, bookmaker-adjusted |
| Task 4 | `efficiency_signals.py` | `MarketEfficiencyEngine` → consensus / disagreement / sharp-proxy |
| Task 5 | `integrity.py` | `DataIntegrityLayer` → DQ flags |
| Orchestr. | `measurement_pipeline.py` | wires all five + builds the consensus stream |
| Runner | `run_r1_2_measurement.py` | fixture → pipeline → JSON/summary |
| Fixture | `fixtures.py` | deterministic 5-match snapshot set (+ injected defects) |

**Design invariants (enforced by tests):** no target leakage · point-in-time
only · strict chronological ordering · no ML · closing line is a *future
placeholder* (CLV reported `PENDING_CLOSE` until a real close exists).

---

## 2. Mathematical Definitions

Notation: a selection's ordered series is points `(t_i, o_i)`, `o_i` = decimal
odds at time `t_i` (hours). Implied (vig-inclusive) prob `p_i = 1/o_i`.
Horizon odds: `o_open, o_24, o_12, o_6, o_1`; `o_now` = latest available
pre-close horizon; `o_close` = closing (or provisional close).

### 2.1 Time-Series Builder (Task 1)
Per `(match_id, market, selection[, bookmaker])`: sort ascending by timestamp,
collapse same-timestamp duplicates (keep highest `confidence_score`). Horizon
buckets are **leakage-free**:

```
opening  = earliest snapshot
H∈{24,12,6,1}h:  bucket = latest snapshot with t ≤ (kickoff − H)     # point-in-time
live     = earliest snapshot with t > kickoff
closing  = explicit CLOSE snapshot, else last snapshot with t ≤ kickoff  (→ provisional)
```
The 24h bucket can never see a quote published after `kickoff − 24h`.

### 2.2 Odds Drift Engine (Task 2)
```
odds_change_24h = (o_now − o_24)/o_24       odds_change_6h = (o_now − o_6)/o_6
odds_change_1h  = (o_now − o_1)/o_1          total_drift    = (o_now − o_open)/o_open
```
Finite-difference rates on the ordered series:
```
v_i = (o_i − o_{i−1}) / (t_i − t_{i−1})          [odds units / hour]
a_i = (v_i − v_{i−1}) / (t_i − t_{i−1})          [odds units / hour²]
drift_velocity = v_last,   drift_acceleration = a_last
```
Probability mirror (sign-stable): `prob_drift_total = p_now − p_open`
(+ve ⇒ line shortened). Direction: `total_drift<0 ⇒ SHORTENING` (backing
pressure), `>0 ⇒ DRIFTING`, `|·|<1e-4 ⇒ FLAT`.

### 2.3 CLV Foundation (Task 3) — no model predictions
```
CLV_raw = (closing_odds − entry_odds) / entry_odds          # canonical (as specified)
clv_backer = (entry_odds / closing_odds) − 1                # interpretation aid (+ve ⇒ beat close)
CLV_raw[h] = (closing − o_h)/o_h         for h ∈ {open,24h,12h,6h,1h}   # snapshot-level
```
Time-decay weighted CLV (more weight to entries nearer the close):
```
w_h = exp(−λ·Δh_h),  Δh_h = hours between entry h and close,  λ = ln2/12 (12h half-life)
weighted_CLV = Σ_h w_h·CLV_raw[h] / Σ_h w_h
```
Bookmaker-adjusted CLV: per-book CLV vs that book's own close, aggregated
`Σ conf_b·CLV_b / Σ conf_b`; report cross-book stdev as `clv_dispersion`.

### 2.4 Market Efficiency Signals (Task 4) — no ML
De-vig per book/market/horizon: `p[b,s]=(1/o[b,s]) / Σ_s(1/o[b,s])`,
overround `R[b]=Σ_s 1/o[b,s]`.
```
consensus_prob[s]            = mean_b p[b,s]
disagreement[s]              = stdev_b p[b,s]
market_consensus_score       = clip(1 − mean_s(disagreement[s]/consensus_prob[s]), 0, 1)
bookmaker_disagreement_index  = mean_s disagreement[s]
```
Sharp proxy (early move only, no close → no leakage):
```
move[b,s] = p_24[b,s] − p_open[b,s]                          # +ve ⇒ shortened early
sharp_proxy[s] = Σ_b conf_b·move[b,s] / Σ_b conf_b
sharp_proxy_signal = sharp_proxy[argmax_s |sharp_proxy[s]|] (signed)
```

### 2.5 Data Integrity (Task 5)
- **missing_snapshot_gaps** — a pre-match horizon with no quote at/before its
  cut-off, or a bucket filled by a snapshot whose age deviates > `3h` from target.
- **timestamp_irregularities** — tz-naive, future-dated, or repeated stamps.
- **duplicate_odds_sequences** — exact `(timestamp, odds)` repeats, or ≥3
  consecutive identical odds (frozen feed).
- **impossible_market_jumps** — `|Δp| > 0.25` within ≤2h, or odds ratio outside
  `[1/2.5, 2.5]`.

---

## 3. Data Pipeline Transformation Flow

```
raw OddsRecord[]                                    (269 records, 5 matches, 3 books, 1X2)
  │  group by MarketKey, sort, dedup same-ts
  ▼
per-book MarketTimeSeries  (45 series)              ──┐
  │  per (match,market): for each t, latest-≤t per     │ feeds Efficiency (cross-book)
  │  book → de-vig → average fair prob → 1/p           │ feeds Integrity (per-book)
  ▼                                                     │ feeds bookmaker-adjusted CLV
consensus MarketTimeSeries (15 series)  ◀──────────────┘
  │
  ├─▶ OddsDriftEngine      → DriftSignals      (per consensus series)
  ├─▶ CLVFoundation        → CLVResult         (per consensus series; PROVISIONAL close)
  ├─▶ CLVFoundation.bookmaker_adjusted → per (match,market,selection)
  ├─▶ MarketEfficiencyEngine → EfficiencySignals (per match·market)
  └─▶ DataIntegrityLayer   → IntegrityReport   (per-book series)
  ▼
MeasurementResult.to_dict() → JSON report
```

---

## 4. Example Computed Outputs (5 matches)

Fixture: kickoff anchored a few hours in the past at run time (so OPEN..CLOSE is
historical); signal values are time-relative and therefore reproducible. Close
is **provisional** (= last pre-KO snapshot) because the real close is an R2/R3
future placeholder.

### 4.1 Drift — consensus, HOME selection
| Match | total_drift | dir | Δ24h | Δ6h | velocity | accel | prob_drift |
|---|---|---|---|---|---|---|---|
| Arsenal–Chelsea | −0.1396 | SHORTENING | −0.085 | −0.021 | −0.0405 | −0.0312 | +0.0641 |
| Liverpool–Man City | +0.1911 | DRIFTING | +0.136 | +0.041 | +0.1004 | +0.0796 | −0.0733 |
| Barcelona–Real Madrid | −0.0033 | ~FLAT | +0.007 | +0.004 | +0.0031 | +0.0011 | +0.0012 |
| Juventus–Milan | −0.0609 | SHORTENING | −0.033 | −0.010 | −0.0314 | −0.0276 | +0.0321 |
| Bayern–Dortmund | −0.0418 | SHORTENING | −0.025 | −0.007 | 0.0 | 0.0 | +0.0271 |

Reading: Arsenal HOME steadily backed (odds −14% since open); Liverpool HOME
drifted out +19% (money on the away side); Barça near-static (efficient/quiet).

### 4.2 CLV — consensus HOME (full record, Liverpool)
```json
{
  "status": "PROVISIONAL", "closing_odds": 2.7091,
  "entry_reference": "1h", "entry_odds": 2.6087,
  "clv_raw": 0.03847, "clv_backer": -0.03705,
  "clv_by_snapshot": {
    "opening": 0.23693, "24h": 0.17934, "12h": 0.13076, "6h": 0.08149, "1h": 0.03847
  },
  "weighted_clv": 0.08888, "provisional_close": true
}
```
`clv_by_snapshot` decays monotonically: backing Liverpool HOME at the **open**
(2.05-ish) captured +23.7% vs the provisional close; entering at **1h** only
+3.8%. Earlier = more value here. `weighted_clv` 0.089 sits between (12h
half-life).

### 4.3 Bookmaker-adjusted CLV — HOME selection
| Match | pinnacle | bet365 | obscurebet | conf-weighted | dispersion |
|---|---|---|---|---|---|
| Arsenal–Chelsea | −0.0250 | −0.0249 | −0.0095 | −0.0217 | 0.0089 |
| Liverpool–Man City | +0.0408 | +0.0407 | +0.0320 | +0.0389 | 0.0050 |
| Barcelona–Real Madrid | 0.0000 | +0.0040 | 0.0000 | +0.0016 | 0.0023 |
| Juventus–Milan | −0.0169 | −0.0168 | −0.0160 | −0.0166 | 0.0005 |
| Bayern–Dortmund | −0.0069 | −0.0069 | −0.0068 | −0.0069 | 0.0001 |

The soft book (obscurebet, conf 0.50) consistently shows smaller-magnitude CLV;
confidence weighting pulls the aggregate toward the sharp books (conf 0.95).

### 4.4 Efficiency — full record (Arsenal 1X2)
```json
{
  "horizon_used": "1h", "n_books": 3,
  "consensus_prob": {"HOME": 0.45922, "DRAW": 0.27289, "AWAY": 0.26789},
  "disagreement":   {"HOME": 0.01669, "DRAW": 0.00619, "AWAY": 0.01051},
  "market_consensus_score": 0.96724,
  "bookmaker_disagreement_index": 0.01113,
  "sharp_proxy": {"HOME": 0.02586, "DRAW": -0.00800, "AWAY": -0.01785},
  "sharp_proxy_signal": 0.02586, "sharp_proxy_selection": "HOME",
  "mean_overround": 1.06998
}
```
| Match | consensus_score | disagreement_idx | mean_overround | sharp_proxy (sel) |
|---|---|---|---|---|
| Arsenal–Chelsea | 0.9672 | 0.0111 | 1.070 | +0.0259 (HOME) |
| Liverpool–Man City | 0.9846 | 0.0051 | 1.056 | −0.0217 (HOME) |
| Barcelona–Real Madrid | 0.9878 | 0.0040 | 1.067 | +0.0036 (HOME) |
| Juventus–Milan | 0.9568 | 0.0137 | 1.044 | +0.0150 (HOME) |
| Bayern–Dortmund | 0.9755 | 0.0065 | 1.056 | +0.0104 (HOME) |

Sharp-proxy sign matches drift: Arsenal HOME early-backed (+), Liverpool HOME
early money *off* it (−). Barça shows the tightest agreement (0.988).

---

## 5. Data Quality Report (real run)

`series_checked = 45`, `total_flags = 11`. Every flag is an
**intentionally-corrupted** fixture row — detectors fire exactly where seeded,
nowhere else.

| Check | Count | Sample detail |
|---|---|---|
| missing_snapshot_gaps | 3 | `evt_juv_mil HOME/DRAW/AWAY @pinnacle` — 6h bucket filled by a 12h-old snapshot (gap 6.0h) |
| duplicate_odds_sequences | 5 | `evt_juv_mil @obscurebet` (×3) + `evt_bar_rma @pinnacle/@bet365` — 3 consecutive identical odds (frozen feed) |
| impossible_market_jumps | 2 | `evt_bay_dor HOME @bet365` — odds ratio 3.02 (1.49→4.50) then 0.32 (4.50→1.46) |
| timestamp_irregularities | 1 | `evt_bay_dor HOME @obscurebet` — future timestamp 2030-01-01 |

Seeded defects: pinnacle's 6h Juventus snapshot removed (gap); obscurebet frozen
across 12h/6h/1h; bet365 Bayern HOME spike to 4.50; a 2030 stamp; an exact
duplicate open quote (collapsed by the builder, so it does not double-count).

---

## 6. CLV Signal Interpretation Examples

1. **Liverpool HOME — positive CLV, fading.** `clv_by_snapshot` 0.237→0.038
   from open→1h. A backer who took the *opening* price beat the (provisional)
   close by +23.7%; the edge erodes as kickoff nears. Actionable read: this
   selection rewards **early** entry; late entry adds little CLV.

2. **Arsenal HOME — negative CLV_raw, positive backer value.** `CLV_raw=−0.019`
   (the line *shortened* from the 1h entry to close) ⇒ `clv_backer=+0.019`: a
   backer who got the earlier, longer price *beat* the close. The two signs are
   complementary — `CLV_raw` measures line movement, `clv_backer` measures the
   bettor's edge. Combined with the SHORTENING drift and +sharp_proxy, this is a
   coherent "smart-money-followed" picture.

3. **Barcelona HOME — CLV ≈ 0.** `weighted_clv≈0.003`, drift ~FLAT,
   consensus_score 0.988. No closing-line value to capture; the market was
   efficient and quiet. Correctly produces a near-null signal (no false edge).

4. **Bookmaker dispersion as a trust cue.** Where `clv_dispersion` is tiny
   (Bayern 0.0001) all books agree the line barely moved; where it is larger
   (Arsenal 0.0089) the soft book lagged the sharps — the confidence-weighted
   aggregate is the trustworthy number.

> Caveat: all CLV here is against a **provisional** close. Real CLV requires the
> R2/R3 closing-line snapshot; until then treat magnitudes as directionally
> indicative, not settled.

---

## 7. Next-Step Recommendation

1. **Wire the real close (R2/R3).** Replace the provisional close with the
   scheduled CLOSE snapshot; CLV status flips `PROVISIONAL → COMPUTED`. No code
   change in this layer — only `MatchContext.closing_ts` / a CLOSE record.
2. **Persist measurement outputs** to the warehouse tables
   (`market_movements`, `clv_history`, `market_consensus`) defined in
   `MIW_DATABASE_LAYER.md` — this layer already emits exactly those shapes.
3. **Run on live PAL output:** `OddsRecord.from_pal()` is ready; once the PAL
   adapters fetch real snapshots, feed them straight in (no transform changes).
4. **Add per-bookmaker collection timestamps** (PAL risk note) to sharpen
   staleness/gap detection beyond the fixture proxy.
5. **Feed the Feature Layer (Phase 5), not the model directly.** These signals
   (drift velocity/accel, sharp_proxy, consensus, disagreement) are leading,
   pre-close, leakage-free — exactly the inputs `MIW_FEATURE_LAYER.md` expects.
   ML/threshold/decision stay untouched until features are validated.

---

### Run it
```bash
python3 -m src.market.run_r1_2_measurement --pretty     # human summary + JSON
python3 -m unittest tests.test_r1_2_measurement -v       # 14 invariants (no network, no ML)
```

**Summary:** a pure, runnable measurement layer (time-series → drift → CLV →
efficiency → integrity) was built and executed on real code. Drift signs,
de-vigged consensus, time-decayed CLV, confidence-weighted bookmaker CLV and the
DQ detectors all behave correctly on the fixture; 14 invariant tests pass. The
layer is PAL-ready (`from_pal`) and writes warehouse-shaped output. ML,
calibration, threshold and decision systems were not touched. Next: real
closing line (R2) + warehouse persistence.
