# M3.2 — Truth → Edge Wiring (discount-only)

> **Status:** Implemented (`src/market/truth/edge_wiring.py`,
> `tests/test_truth_edge_wiring.py`). **Constraint honored:** no redesign of the
> edge kernel. R1.2, R1.3 internals, OddsRecord, CLV, Portfolio, and thresholds
> are untouched. Pure, network-free; 10 wiring tests (70 total in suite).

---

## 1. Architecture

```
Truth Store (M2) ─▶ TruthAdapter (M3) ─┬─▶ R1.2 Measurement ─▶ R1.3 Edge ─▶ EdgeResult
                                       │                                        │
                                       └─▶ truth_meta {(m,mkt,sel): TruthMeta}  │
                                                          │                     ▼
                                                          └──▶ TruthEdgeAdjuster (M3.2)
                                                                  discount-only → TruthAdjustedEdge
```
`TruthEdgeAdjuster` is a **new additive post-processing layer**. It reads an
R1.3 `EdgeResult` + the selection's `TruthMeta`, applies a discount to the
headline edge, then **reuses the unchanged** `EdgeQualityScorer` and
`SignalClassifier` to recompute EQS/tier. The edge kernel is never modified.

`TruthMeta` (produced by M3's adapter, additively extended) carries:
`confidence` (= `truth_quality`), `provenance`, `truth_efficiency`
(cross-book agreement = `1 − sigma/scale`), and `sharp_consensus_strength`
(sharp trust-share × agreement).

---

## 2. Truth Adjustment Logic

Three orthogonal discount factors, each in `(0, 1]`:

| Layer | Input | High → | Low → |
|---|---|---|---|
| Confidence | `truth.confidence` | minimal discount (~1) | aggressive discount (→floor) |
| Provenance | `truth.provenance` | OBSERVED = 1.0 | PARTIAL 0.80, RECONSTRUCTED 0.50 |
| Sharp consensus | `sharp_consensus_strength` | small discount (~1) | large discount (→floor) |

Their product is the total truth discount, applied multiplicatively to the
headline edge. Confirming/strong truth ⇒ discount near 1; weak/unobserved truth
⇒ strong discount toward the floor.

---

## 3. Discount Formulas

```
confidence_discount      = c_floor + (1 − c_floor)·clip(confidence, 0, 1)     c_floor = 0.30
provenance_discount      = { OBSERVED:1.00, PARTIAL:0.80, RECONSTRUCTED:0.50, unknown:0.50 }
sharp_consensus_discount = s_floor + (1 − s_floor)·clip(sharp_strength, 0, 1)  s_floor = 0.40

truth_discount   = clip( confidence_discount · provenance_discount · sharp_consensus_discount , 0, 1 )
edge_after_truth = edge_before_truth · truth_discount        # edge_before = sharp_adjusted_edge (R1.3)
```
**Discount-only guarantee:** every factor ≤ 1 ⇒ `truth_discount ≤ 1` ⇒ for a
positive edge `0 ≤ edge_after ≤ edge_before`. Edge is never increased. EQS/tier
are recomputed on `edge_after` with the existing kernel components, so a tier can
only stay equal or drop — **never improve.** Missing `TruthMeta` ⇒ conservative
strong discount (treated as zero-confidence / unknown provenance).

---

## 4. Monitoring Outputs

`TruthAdjustedEdge` reports (required): `edge_before_truth`, `edge_after_truth`,
`truth_discount`, `confidence_discount`, `provenance_discount`,
`sharp_consensus_discount`, plus `eqs_before/after`, `tier_before/after`,
`provenance`, `truth_confidence`.

---

## 5. Validation Tests

| Property | Test |
|---|---|
| discount factors in (0,1] | `test_discount_factors_bounded` |
| **edge never increased** (positive) | `test_discount_only_positive_edge` |
| confidence band monotonic | `test_confidence_monotonic` |
| provenance OBSERVED > RECONSTRUCTED | `test_provenance_mapping` |
| sharp agreement monotonic | `test_sharp_consensus_monotonic` |
| **tier never improves** | `test_tier_never_improves` |
| low trust demotes EQS/tier | `test_low_trust_demotes` |
| monitoring fields present | `test_monitoring_fields` |
| missing meta → conservative | `test_missing_meta_conservative` |
| batch | `test_batch` |

---

## 6. Failure Modes

| Failure | Detection | Response |
|---|---|---|
| Missing `TruthMeta` for a selection | `meta is None` | conservative strong discount (not a pass-through) |
| Unknown provenance label | not in factor map | `provenance_unknown = 0.50` (conservative) |
| Over-discounting kills real edges | EQS/tier before-vs-after monitoring | tune floors via `TruthEdgeConfig` (governance), not in kernel |
| Truth confidence mis-calibrated | HYBRID divergence (M3) + before/after gap | gate config; recalibrate truth confidence (M2/F15) |

---

## 7. Implementation Roadmap

| Step | Scope | Status |
|---|---|---|
| M3.2.0 | `TruthEdgeAdjuster` discount layer + reuse EQS/classifier | ✅ done |
| M3.2.1 | Enrich `TruthMeta` (truth_efficiency, sharp_consensus_strength) | ✅ done |
| M3.2.2 | Wiring tests (discount-only, bands, provenance, tier-monotone) | ✅ done |
| M3.2.3 | Surface `TruthAdjustedEdge` in monitoring/Telegram (M6) | next |
| M3.2.4 | Feed `truth_confidence` into R1.3 `calibration_quality` at call site (no kernel change) | optional next |

> The kernel stays discount-only and untouched: M3.2 multiplies its output by a
> truth factor ≤ 1 and re-scores with the same scorer/classifier. No edge is ever
> inflated; no existing module is redesigned.
