# MIW R1.1 — First Live Provider Integration & Snapshot Validation

> Real, runnable code was written and the pipeline was actually executed. Changes are isolated to `src/db/providers/` + `config/providers.yaml`. ML/CLV/threshold/decision layers untouched. The report below is the real JSON output of the code, not fabricated.

## Security / environment notes
- The API-Football key was pasted in chat -> it is now visible in history. Regenerate it on api-football.com when done. The key is NEVER written to any file; code reads it only from the `API_FOOTBALL_KEY` env var (so it cannot leak into the repo).
- This sandbox has no outbound network (`Name or service not known`). Even with a valid key, api-football.com is unreachable here. So the pipeline ran via automatic failover on a recorded fixture. Run live on your own machine (network + key).

## 1. Implementation Summary
| File | Content |
|---|---|
| core.py | OddsRecord schema, SnapshotType/MarketType enums, OddsProviderInterface (ABC), normalization helpers |
| api_football.py | APIFootballProvider (chosen provider; live HTTP, key from env) + _af_selection mapping |
| the_odds_api.py | TheOddsAPIFreeProvider (fixture-backed) + FallbackStubProvider |
| registry.py | ProviderRegistry (register/get/health_check_all/fallback/get_active) + YAML loader |
| snapshot_validation.py | SnapshotEngine (dedup) + DataQuality + build_report |
| config/providers.yaml | primary: api_football; fallback: the_odds_api -> stub |
| run_r1_1_validation.py | Pipeline runner -> JSON report |

Downstream sees only `registry.get_active()`; since primary (api_football) was unhealthy, the chain failed over to the_odds_api and produced a normalized OddsRecord stream — provider swapped without code change.

## 2. Provider Mapping Logic
| Field | API-Football | The Odds API |
|---|---|---|
| Market | "Match Winner" -> 1X2; "Goals Over/Under" -> O/U | h2h -> 1X2; totals -> O/U; draw_no_bet -> DNB |
| Selection (1X2) | Home/Draw/Away -> HOME/DRAW/AWAY | team name/Draw -> HOME/DRAW/AWAY |
| Selection (O/U) | "Over 2.5" -> OVER_2.5 | name+point -> OVER_2.5 / UNDER_2.5 |
| Odds | decimal float, > 1.0 | decimal float, > 1.0 |
| Time | fixture.date -> UTC tz-aware | last_update -> UTC tz-aware |
| Bookmaker | canonical map (Bet365/bet 365 -> bet365); unknown -> conf 0.50 | same map |

## 3. Snapshot Validation Results (real run)
| Metric | Value |
|---|---|
| Active provider | the_odds_api (via failover) |
| Failover occurred | YES (api_football: "API_FOOTBALL_KEY not set") |
| Matches processed (>=3 required) | 4 |
| Matches with data | 4 |
| Provider success rate | 1.0 |
| Snapshot completeness ratio | 0.875 (evt_new_avl missing O/U -> 7/8 cells) |
| Raw / unique / duplicates removed | 31 / 28 / 3 |
| Markets mapped | 1X2, O/U, DNB |
| Timestamps | all normalized to UTC tz-aware |

Dedup: the intentionally duplicated bet365 h2h block (3 records) was detected and dropped (31 -> 28). No duplicate OddsRecord remains.

Sample 5 OddsRecords:
```json
[
  {"match_id":"evt_ars_che","bookmaker":"pinnacle","market":"1X2","selection":"HOME","odds":2.1,"timestamp":"2026-06-09T08:30:00+00:00","snapshot_type":"OPEN","source_id":"the_odds_api","confidence_score":0.95},
  {"match_id":"evt_ars_che","market":"1X2","selection":"DRAW","odds":3.3},
  {"match_id":"evt_ars_che","market":"1X2","selection":"AWAY","odds":3.4},
  {"match_id":"evt_ars_che","market":"O/U","selection":"OVER_2.5","odds":1.95},
  {"match_id":"evt_ars_che","market":"O/U","selection":"UNDER_2.5","odds":1.9}
]
```

## 4. Data Quality Report
| Check | Finding | Source (intentional in fixture) |
|---|---|---|
| Missing odds | 1 | evt_liv_mci / bet365 / "Man City" price null -> normalization error, record dropped |
| Impossible odds (>50) | 1 | evt_mun_tot / obscurebet / Tottenham 75.0 -> flagged |
| Out-of-range (<=1.01) | 0 | - |
| Stale timestamp (>6h) | 5 | evt_mun_tot / pinnacle last_update 1 day old -> 3x 1X2 + 2x O/U |
| Unknown bookmaker | 1 (obscurebet) | confidence 0.50 instead of 0.95 |

All DQ checks fired on the intentionally corrupted rows — detectors work.

## 5. Risk Notes
- Live validation not yet done: network is off here; real API-Football response (schema/market names/quota behavior) must be confirmed in the field. Mapping follows API-Football v3 docs; minor field diffs may surface live.
- API-Football quota: free tier daily request cap; snapshot cadence (T-24h..CLOSE) must respect quota.
- Key hygiene: rotate the chat-shared key; live use only via env var / secret manager.
- Snapshot time: API-Football odds lack per-bookmaker last_update; fixture.date used as a proxy -> add a collection timestamp for live staleness detection.
- DNB completeness: DNB is currently out of core markets; completeness measured on 1X2+O/U only.

## 6. Next Step Recommendation
1. Live validation (your machine):
```bash
export API_FOOTBALL_KEY=***   # the rotated key, NOT the one from chat
cd src/db/providers && python3 run_r1_1_validation.py
# expect provider_health.api_football.healthy = true; mode=live
```
2. Save one real API-Football fixture for regression -> fixtures/api_football_sample.json.
3. Add per-bookmaker collection timestamp (staleness accuracy).
4. Wire the snapshot scheduler (T-24h..CLOSE) into R2: Historical Odds Warehouse MVP.
5. Add contract tests to CI (no-code provider swap + schema + failover + rate-limit).

---
Summary: concrete adapter (API-Football + The Odds API) + registry + snapshot validation + data-quality + report were actually built and executed. Failover is real (api_football -> the_odds_api), dedup dropped 3 duplicates, DQ caught 4 issues. The only missing piece is the live API-Football call, blocked by no-network in this sandbox; complete it on your machine with key + network in one command. Next: R2.
