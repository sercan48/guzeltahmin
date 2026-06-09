# M6 — Telegram Output & Explainability Layer

> **Status:** Implemented (`app/bot/paper_formatter.py`,
> `tests/test_paper_formatter.py`). **Presentation only.** No prediction / R1.2 /
> R1.3 / Truth Store / orchestrator changes; no bankroll / Kelly / stake; no real
> Telegram sending; no real betting. Existing `app/bot/formatters.py` left
> untouched (new additive module). Pure, network-free; 14 tests (117 total).

---

## 1. Implementation Summary

A new presentation module renders already-computed signals into human-readable
Telegram messages with full explainability, and decides channel routing **as
metadata only** (it never sends). It reads the richer M3.2 `TruthAdjustedEdge`
(full edge decomposition + provenance) or, in degraded form, the M5
`PaperSignal`.

---

## 2. Architecture

```
TruthAdjustedEdge (M3.2)  ─┐
                           ├─▶ SignalView ─▶ PaperSignalFormatter ─▶ (message, Channel)
PaperSignal (M5, degraded)─┘                       │
                                                   └─▶ FormatterMetrics
```
`SignalView` is the presentation DTO (only what the message needs).
`from_adjusted` gives full decomposition; `from_paper` degrades gracefully
(raw edge / discount shown `n/a`, provenance `UNKNOWN`). The formatter is a pure
function of the view (deterministic). **No sending, no betting.**

---

## 3. Message Format Specification

```
⚽ <match> — <market>
PICK: <selection>

Tier: <A/B/...>
Edge: <final %>
Confidence: <ECS>
Truth Confidence: <truth conf>
Source: <OBSERVED/PARTIAL/RECONSTRUCTED/UNKNOWN>
Time: <ISO>

Edge decomposition:
  Raw Edge: <%>
  Truth Discount: <factor>
  Final Edge: <%>

[⚠️ provenance warning if PARTIAL/RECONSTRUCTED]

WHY THIS PICK
• Model probability exceeds market probability   (raw_edge > 0)
• Strong truth confidence                        (truth_confidence >= 0.70)
• Sharp consensus present                        (sharp_consensus_discount >= 0.70)
• No lifecycle restrictions active               (signal cleared the state gate)
```
Provenance warnings: RECONSTRUCTED → "⚠️ Reconstructed market truth. Use with
caution."; PARTIAL → "⚠️ Partial market truth. Use with caution."; OBSERVED →
none.

---

## 4. Routing Design (metadata only)

| Tier | Channel |
|---|---|
| TIER_S, TIER_A | VIP |
| TIER_B | STANDARD |
| TIER_C | MONITORING |
| REJECT | NONE (suppressed, no message) |

`format_and_route(view)` returns `(message | None, channel)`. REJECT yields
`(None, NONE)`. **No real Telegram dispatch** — wiring channels to the live bot
is a later integration step that consumes this metadata.

---

## 5. Monitoring

`FormatterMetrics`: `formatted_messages`, `rejected_messages`,
`routing_distribution` (per-channel counts).

---

## 6. Failure Modes

| Failure | Handling |
|---|---|
| Missing decomposition (PaperSignal only) | `from_paper` shows raw/discount `n/a`, provenance `UNKNOWN` |
| REJECT tier | suppressed (no message), `rejected_messages++` |
| Unknown tier label | routes to NONE (suppressed) |
| Low truth confidence / weak sharp | corresponding "why" reasons omitted (message still valid) |

---

## 7. Test Report

| Area | Tests | Result |
|---|---|---|
| formatting / explainability fields | message contains Tier/Edge/Confidence/Truth Confidence/Source | ✅ |
| edge decomposition | Raw/Truth Discount/Final present | ✅ |
| WHY THIS PICK | reasons present / weak omitted | ✅ |
| provenance warnings | RECONSTRUCTED/PARTIAL warn; OBSERVED clean | ✅ |
| tier routing | S/A→VIP, B→STANDARD, C→MONITORING, REJECT→NONE | ✅ |
| reject suppression | None + rejected_messages++ | ✅ |
| monitoring | routing_distribution counts | ✅ |
| determinism | identical output for identical view | ✅ |
| degraded view / no-stake | from_paper; no stake/bankroll/kelly in output | ✅ |

14 tests; full suite 117 green; no existing module changed.

---

## 8. Definition of Done

- [x] PaperSignal/TruthAdjustedEdge → human-readable Telegram message
- [x] Explainability block (Tier, Edge, Confidence, Truth Confidence, Provenance, Time)
- [x] Edge decomposition (Raw / Truth Discount / Final)
- [x] WHY THIS PICK transparency section
- [x] Provenance warnings for PARTIAL / RECONSTRUCTED
- [x] Tier → channel routing as **metadata only** (no real sending)
- [x] Monitoring metrics (formatted / rejected / routing_distribution)
- [x] Tests: formatting, explainability, provenance, routing, reject suppression, determinism
- [x] No prediction/R1.2/R1.3/Truth/orchestrator changes; no bankroll/Kelly/stake; paper only

> Next (integration, out of M6 scope): wire `Channel` to the live bot's channel
> IDs in `app/telegram_bot.py` to actually dispatch — a thin send step that
> consumes this layer's `(message, channel)` output.
