# M3 — Truth Rule Enforcement Layer

> **Status:** Implemented (`src/market/truth/adapter.py`, `tests/test_truth_adapter.py`).
> **Constraint honored:** no redesign of R1.2 / R1.3 / OddsRecord. M3 is a thin
> adapter only. Pure, network-free; 7 enforcement tests (60 total in the truth +
> R1.2/R1.3 suite).

---

## 1. Executive Summary

R1.2 consumed raw provider odds, violating **"NO RAW PROVIDER DATA
DOWNSTREAM."** M3 inserts a single sanctioned bridge — `TruthAdapter` — between
the Truth Store (M2) and R1.2. It converts canonical truth (`p_truth`/`o_truth`/
`confidence`/`provenance`) into the exact `OddsRecord` stream R1.2 already
accepts, so **no existing module changes**. Three modes (truth-only / hybrid /
legacy) give a safe migration path with instant rollback.

---

## 2. Truth Adapter Architecture

```
Providers ─▶ Canonicalization (M1) ─▶ Truth Store (M2) ─▶ TruthAdapter (M3) ─▶ R1.2 ─▶ R1.3 ─▶ …
                                                              ▲
                                          (the ONLY path raw odds may reach R1.2+)
FORBIDDEN:  Provider ─▶ R1.2 / R1.3 / CLV / Portfolio
```
- **Conversion:** each truth row → `OddsRecord(bookmaker="truth", odds=o_truth,
  timestamp=as_of, snapshot_type=…, confidence_score=confidence,
  source_id="truth:<PROVENANCE>")`. R1.2 runs unchanged on this stream.
- **Propagation without schema change:** confidence rides in
  `OddsRecord.confidence_score`; provenance rides in `source_id`. A parallel
  `truth_meta: {(match,market,sel) → TruthMeta(confidence, provenance, as_of)}`
  exposes both explicitly for layers that want them (e.g. R1.3
  `calibration_quality`) — `OddsRecord` is untouched.
- **Point-in-time:** `build_inputs(as_of=t)` only emits truth with `as_of ≤ t`
  (inherits the M2 leakage-free read).

### Modes
| Mode | Behaviour | Use |
|---|---|---|
| `TRUTH_ONLY` | downstream sees only truth-sourced records | **production target** |
| `HYBRID` | truth is canonical; raw consensus computed *only to validate* (divergence reported, never fed downstream) | migration / monitoring |
| `LEGACY` | passthrough of raw provider records | temporary, pre-migration |

---

## 3. Migration Strategy

**Current flow (pre-M3):** `Provider → R1.2 → R1.3` (raw odds downstream).
**Target flow:** `Provider → M1 → M2 → TruthAdapter(TRUTH_ONLY) → R1.2 → R1.3`.

**Rollout:**
1. Ship `LEGACY` (passthrough) — behaviour-identical to today, zero risk.
2. Switch monitored paths to `HYBRID` — run on truth, diff vs raw consensus;
   alert if `max_abs_gap` exceeds a band. Builds confidence the truth stream
   matches reality.
3. Flip to `TRUTH_ONLY` once HYBRID divergence is within tolerance over a window.
4. Remove `LEGACY` once no caller uses it.

**Rollback:** set `mode = LEGACY` (one flag) — instantly reverts to raw
passthrough with no data migration. HYBRID is also a safe resting state.

---

## 4. Validation Strategy (implemented tests)

| Requirement | Test |
|---|---|
| identical outputs when truth == provider | `test_identical_when_truth_equals_provider` (vig-free ⇒ de-vig identity ⇒ drift matches legacy) |
| confidence propagation | `test_confidence_propagation` (`confidence_score` == truth confidence; meta carries it) |
| provenance propagation | `test_provenance_propagation` (`source_id` = `truth:OBSERVED`; meta provenance) |
| point-in-time correctness | `test_point_in_time_no_leakage` (`as_of` cutoff respected) |
| no leakage | same test (CLOSE/T-1h excluded before their time) |
| hybrid is monitoring-only | `test_hybrid_validation_zero_gap_when_identical` |
| legacy guard | `test_legacy_requires_raw` |

---

## 5. Failure Modes

| Failure | Detection | Response |
|---|---|---|
| Truth Store empty for a match | `build_inputs` emits nothing | no signal (fail-safe); HYBRID/LEGACY bridge during ramp |
| Truth ≠ raw beyond tolerance (bad de-vig / stale book) | HYBRID `max_abs_gap` | alert; stay HYBRID/LEGACY; do not flip to TRUTH_ONLY |
| Missing kickoff context | match skipped (only contexted matches emitted) | supply `MatchContext`; logged |
| Provenance/confidence dropped downstream | carried in `source_id`/`confidence_score` + `truth_meta` | covered by propagation tests |
| Accidental raw→R1.2 path | architectural review / single entry point | all R1.2 inputs must come from `TruthAdapter` |

---

## 6. Implementation Roadmap

| Step | Scope | Status |
|---|---|---|
| M3.0 | `TruthAdapter` (truth→OddsRecord), 3 modes, meta propagation | ✅ done |
| M3.1 | Enforcement tests (identity, confidence, provenance, PIT, no-leak) | ✅ done |
| M3.2 | Wire R1.3 `calibration_quality`/`market_prob` from `truth_meta` + truth efficiency | next |
| M3.3 | HYBRID divergence dashboards + alert thresholds (ties to F14/observability) | next |
| M3.4 | Flip production callers to `TRUTH_ONLY`; deprecate `LEGACY` | after HYBRID soak |

> Note: M3 enforces the boundary at the adapter; it cannot prevent a rogue
> import at the language level. The contract is: **every R1.2+ input originates
> from `TruthAdapter`.** A future lint/contract test can assert no provider
> module is imported by R1.2/R1.3/CLV/portfolio.
