# MIW Phase 9 — Historical Odds Warehouse Implementation Plan

> **Scope.** A concrete, staged plan to take the MIW infrastructure designed in Phases 1-5 (PAL, ingestion, collector/backfill, DB layer) **into production**. This document is **planning + specification, no code**. **Critical principle:** all downstream components (prediction, calibration, threshold, CLV, portfolio) depend only on the PAL normalized schema; none depends on a specific provider implementation. Swapping a provider does not change any downstream code.

---

## 1. Executive Summary

Goal: **start collecting historical + live odds today, using mostly free sources**; build a provider-agnostic warehouse; produce production-quality datasets for CLV analysis, market-movement research, and future model retraining.

- **Strategy:** start with Football-Data (backfill backbone) + API-Football & The Odds API free tiers (live multi-bookmaker); everything behind PAL; premium providers (Pinnacle, Betfair, The Odds API paid) are placeholders that plug into the same interface later.
- **Phases:** MVP (2-3 weeks) -> Beta (1-2 months) -> Production (3-6 months).
- **Warehouse:** MVP uses the existing `guzel_tahmin_miw.db` (SQLite) schema; at scale, partitioned Parquet + SQLite/DuckDB query layer.
- **Priority leagues:** Top-5 Europe + Turkey Super Lig + Norway Eliteserien + Brazil Serie A + MLS.
- **CLV backbone:** opening -> 8 snapshots -> closing chain per match; Pinnacle/Betfair closing is the "true" reference.

---

## 2. Implementation Phases

| Dimension | MVP (2-3 weeks) | Beta (1-2 months) | Production (3-6 months) |
|---|---|---|---|
| Data sources | Football-Data (CSV backfill) + The Odds API free (live) + API-Football free (fixtures) | + additional free odds sources, multi-bookmaker expansion | + premium placeholders (Pinnacle, Betfair, The Odds API paid) behind PAL |
| Markets collected | 1X2, O/U 2.5, Asian Handicap (main line) | + O/U multi-line, AH multi-line, DC, BTTS | + corners, cards, first half, player markets (optional) |
| Snapshot frequency | 8-point schedule (T-72h...Closing) | 8 + T-30m densification | 8 + 15-min live polling in last 2 hours |
| Expected coverage | ~5-8 leagues, ~10 bookmakers | ~15-20 leagues, ~20 bookmakers | 30+ leagues, 30+ bookmakers, sharp included |
| Storage growth | ~50-150 MB/month raw (~15-40 MB compressed) | ~0.3-0.8 GB/month raw | ~1-3 GB/month raw (near upper band with live polling) |

---

## 3. Provider Strategy

All providers sit behind the **PAL interface** (`fetch_fixtures`, `fetch_odds`, `normalize`); output always returns the normalized schema (bookmaker_id, market, selection, decimal_odds, ts, provider, confidence).

| Priority | Provider | Tier | Role | Limit |
|---|---|---|---|---|
| P1 | Football-Data.co.uk | Free | Historical season backfill backbone (closing + some opening, results) | No live, post-match CSV |
| P2 | The Odds API (free) | Free | Live multi-bookmaker snapshots | ~500 credits/month |
| P3 | API-Football (free) | Free | Fixtures, schedule, results, some odds | ~100 req/day |
| P4 | Legal free odds source (scrape) | Free | Gap filling (last resort) | Maintenance burden, fragile |
| - | The Odds API (paid) | Premium placeholder | Wide live coverage | Cost |
| - | Pinnacle | Premium placeholder | Sharp anchor (CLV reference) | Access/cost |
| - | Betfair Exchange | Premium placeholder | True closing / fair odds | Commission/API |

**Failover order:** for a (league, market, snapshot) job, PAL tries P1->P4 in order; on rate-limit/error it falls through to the next. The provider used + confidence are recorded. If multiple providers return, they are merged via `market_consensus`; if a sharp provider (Pinnacle/Betfair) is present it is flagged as the CLV reference.

---

## 4. Snapshot Collection Plan

The schedule is computed backward from kickoff per fixture. **Trigger:** a scheduler (APScheduler/Celery-beat style) enqueues a job at each snapshot time; a worker pulls it. **Closing** is the last odds just before kickoff; the sharp closing is preferred.

| Snapshot | Trigger | Retry behavior | Missing-snapshot recovery |
|---|---|---|---|
| T-72h / T-48h / T-24h | Scheduled queue job | 3 attempts, exponential backoff + jitter | Shift to next snapshot; flag the gap (no fabrication) |
| T-12h / T-6h / T-3h | Scheduled queue job | 4 attempts, backoff + failover | Fill from alternate provider; if still missing, flag gap |
| T-1h | Scheduled + priority queue | 5 attempts, aggressive failover | Derive from nearest valid odds + low confidence |
| Closing | Kickoff-~5m, highest priority | Short-interval intensive retry | **Critical:** if missed, reconstruct from Football-Data CSV closing post-match; confidence lowered |

All snapshots are written to `odds_snapshots`; closing is also copied to `closing_lines`. Missing-snapshot rate is tracked as an operational metric.

---

## 5. Historical Backfill Plan

Past seasons are imported mostly from **Football-Data CSVs** (closing + results + some opening). No forward-filling of missing mid-snapshots from live sources; history is marked with opening/closing only.

- **Source mapping:** league/season -> Football-Data file; team names normalized via an alias dictionary.
- **Confidence scoring:** source reliability + field completeness + provider sharpness (Phase 6 confidence model) yield a 0-1 score per row.
- **Missing data:** never fabricated; `NULL` + gap flag.
- **League priority (order):** 1) Top-5 Europe (EPL, La Liga, Serie A, Bundesliga, Ligue 1) 2) Turkey Super Lig 3) Norway Eliteserien 4) Brazil Serie A 5) MLS.

---

## 6. Storage & Retention

Rough: ~600 odds rows/match (8 snapshots x ~10 bookmakers x ~3 markets x ~2.5 selections), ~200 bytes/row.

| Horizon | Raw (MVP->Prod) | Compressed |
|---|---|---|
| 1 month | ~0.1-0.5 GB | ~30-150 MB |
| 6 months | ~1-3 GB | ~0.3-0.8 GB |
| 12 months | ~2-6 GB + one-time backfill ~2-5 GB | ~0.6-1.5 GB |

- **Partitioning:** `league / season / month` (live); history `league / season`. Parquet partitions at scale.
- **Compression:** Parquet + zstd (at scale); SQLite row-level normalize + VACUUM.
- **Archive:** raw snapshots older than 12 months to cold storage (compressed Parquet); derived CLV dataset and closing_lines stay hot.

---

## 7. Data Quality Pipeline

Validation before write; failing records are **never deleted**, they are quarantined.

| Check | Rule | Action |
|---|---|---|
| Impossible odds | decimal < 1.01, negative, implausible overround (<100% or > cap) | Quarantine |
| Stale odds | ts far older than snapshot; unchanged across many snapshots | Flag + low confidence |
| Duplicate snapshot | same (match, bookmaker, market, selection, snapshot_bucket) | Dedup; keep highest confidence / latest ts |
| Provider conflict | odds diverge beyond tolerance | Keep both with provider tag; `market_consensus` + flag |
| Timestamp anomaly | future ts, kickoff mismatch, out-of-order | Quarantine / fix queue |

**Quarantine workflow:** bad record -> `quarantine` table (reason_code, raw_payload) -> daily review -> auto-reprocess if fixed; otherwise permanent flag. Never reaches the main tables.

---

## 8. CLV Dataset Specification

The row produced for research + training is at the **(match x bookmaker x market x selection)** grain.

| Group | Fields |
|---|---|
| Opening | opening_odds, opening_ts |
| Snapshots | T-72/48/24/12/6/3/1 odds vector + timestamps |
| Closing | closing_odds, closing_ts, closing_source (sharp flag) |
| Line-movement features | total_drift, velocity, volatility, max/min, time-weighted avg, steam flag (F07), RLM flag (F08), disagreement (F10) |
| Bookmaker metadata | type, sharp_flag, trust (F17/Phase 6), region |
| League metadata | efficiency (Phase 6), tier, country |
| Outcome | result (1X2), final score/goals, settled flag |
| CLV labels | realized_clv (vs closing), realized_clv_vs_pinnacle, expected_clv (Phase 6) |

The schema aligns with the existing tables: `odds_snapshots`, `closing_lines`, `market_movements`, `steam_moves`, `clv_history`. The dataset is produced as a materialized view / Parquet export; downstream consumes only this normalized output.

---

## 9. Operational Metrics

| Metric | Definition | Target (SLO) |
|---|---|---|
| Provider uptime | successful calls / total | >=0.98 (>=0.95 for free) |
| Snapshot success rate | captured / scheduled snapshots | >=0.97 |
| Missing snapshot rate | missing / scheduled | <=0.03; closing <=0.005 |
| Average latency | trigger -> write time | p95 < 60 s |
| Warehouse coverage | matches with odds / scheduled matches | >=0.95 in priority leagues |
| Backfill completion | imported seasons / target | 100% of plan |

---

## 10. 12-Week Roadmap

| Week | Deliverable | Dependency | Risk | Success criterion |
|---|---|---|---|---|
| 1 | PAL skeleton + normalized schema + SQLite schema binding | Phase 2-3 | Low | Normalized rows written from one provider |
| 2 | Football-Data backfill importer + team alias mapping | W1 | Medium | Top-5 leagues, 2 seasons loaded |
| 3 | The Odds API free connector + scheduler + 8-snapshot schedule | W1 | Medium | Live snapshots captured (MVP done) |
| 4 | Retry/failover + missing-snapshot recovery + closing reconstruction | W3 | Medium | Closing miss < 1% |
| 5 | Data Quality pipeline + quarantine | W3-4 | Medium | Bad records do not leak to main tables |
| 6 | API-Football fixtures integration + schedule sync | W1 | Low | Fixtures auto-updated |
| 7 | market_consensus + provider-conflict resolution | W5 | Medium | Multiple providers merged |
| 8 | CLV dataset generator (materialized view / Parquet export) | W4-7 | High | Dataset consumable by Phases 6-9 |
| 9 | Operational metrics + dashboard + alerts | W3-8 | Low | SLOs monitored |
| 10 | Beta league expansion (15-20 leagues) + market expansion | W3-5 | Medium | Coverage >=0.95 |
| 11 | Partition/compression + archive policy | W8 | Medium | Storage growth under control |
| 12 | Premium placeholder adapters (Pinnacle/Betfair skeleton) + migration test | W1,W7 | Low | Premium adapter plugs in without breaking downstream |

---

## 11. Risk & Cost Analysis

| Risk | Impact | Mitigation |
|---|---|---|
| Free-tier limits | The Odds API ~500/month, API-Football ~100/day -> coverage tightness | Focus on priority leagues; snapshot budget; call batching (many markets per match in one call) |
| API rate-limit | 429s, missing snapshots | Backoff + jitter + failover; credit counter; prioritize closing |
| Scraping maintenance | fragile, legal risk | Last resort; legal sources only; isolated adapter; graceful skip on break |
| Storage cost | GB/month with live polling | Compression + archive + partition; cool down raw snapshots |
| Premium migration cost | subscription + integration | Zero downstream change thanks to PAL; only a new adapter + key; enable gradually |

---

## 12. Recommended MVP Configuration

- **Sources:** Football-Data (backfill) + The Odds API free (live) + API-Football free (fixtures).
- **Leagues:** Top-5 Europe + Turkey Super Lig (~6 leagues; add Eliteserien if credit budget allows).
- **Markets:** 1X2, O/U 2.5, AH main line.
- **Snapshots:** 8-point (T-72...Closing); closing highest priority.
- **Warehouse:** existing `guzel_tahmin_miw.db` (SQLite) + `league/season/month` logical partition.
- **Bookmakers:** the ~8-10 books The Odds API returns; flag sharp as CLV reference if present.
- **Quality:** full validation + quarantine on from day 1.
- **Output:** daily CLV dataset export -> consumed directly by Phases 6-9.

> **Provider-independence guarantee:** downstream (prediction / calibration / threshold / CLV / portfolio) reads only the PAL normalized schema and the CLV dataset. Adding/removing a provider is just a new adapter + priority/failover setting; no model/decision code changes.
