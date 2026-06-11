# MIW Service — Operations Guide

## Quick Start (dry-run mode)

```bash
# 1. Set required secrets (dry-run only needs Betfair/Pinnacle for data; no Telegram token needed)
export BETFAIR_APP_KEY="your_app_key"
export BETFAIR_SESSION_TOKEN="your_session_token"
export PINNACLE_API_KEY="your_pinnacle_key"

# 2. Run in dry-run mode (no Telegram messages sent)
python3 -m src.market.service.main \
    --dry-run \
    --log-level INFO \
    --scheduler-db scheduler.db \
    --truth-db truth.db \
    --control-db control.db \
    --bridge-db bridge.db
```

Or from Python directly:

```python
from src.market.service import build_runtime, RuntimeConfig, TelegramConfig, SchedulerConfig, ProviderConfig

cfg = RuntimeConfig(
    providers=[
        ProviderConfig(name="betfair"),
        ProviderConfig(name="pinnacle"),
    ],
    scheduler=SchedulerConfig(db_path="scheduler.db", poll_interval_seconds=30.0),
    telegram=TelegramConfig(dry_run=True),   # <-- safe default
    truth_db_path="truth.db",
    control_db_path="control.db",
    bridge_db_path="bridge.db",
)

service = build_runtime(cfg)
service.run()   # blocks; Ctrl-C or SIGTERM for graceful shutdown
```

---

## Required Environment Variables

| Variable | Required for | Description |
|---|---|---|
| `BETFAIR_APP_KEY` | Betfair live data | Application key from Betfair developer portal |
| `BETFAIR_SESSION_TOKEN` | Betfair live data | Session token (refresh via login API) |
| `PINNACLE_API_KEY` | Pinnacle live data | API key from Pinnacle account |
| `TELEGRAM_BOT_TOKEN` | Live publishing only | Bot token from @BotFather |

None of these are read at import time. The service calls `SecretProvider.get()` when
the first request is made. In dry-run mode, `TELEGRAM_BOT_TOKEN` is never accessed.

---

## Switching from Dry-Run to Live Publishing

1. Create a Telegram bot via `@BotFather` and copy the token.
2. Create VIP and standard channels; add the bot as admin.
3. Set the token:
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABCdef..."
   ```
4. Update config:
   ```python
   telegram=TelegramConfig(
       dry_run=False,                      # ← enable live sends
       bot_token_secret="TELEGRAM_BOT_TOKEN",
       vip_channel_id="@your_vip_channel",
       standard_channel_id="@your_std_channel",
       vip_tier_threshold="TIER_A",        # TIER_A + TIER_S → VIP
   )
   ```
5. Start service. Signals with tier ≥ `TIER_A` go to the VIP channel; others to
   the standard channel. Suppressed signals are logged but never sent.

---

## Rotating Provider Secrets Safely

### Betfair session token (expires every ~8h)
```bash
# Obtain new token via Betfair Identity API, then:
export BETFAIR_SESSION_TOKEN="new_token_here"
# Restart the service (SIGTERM → graceful shutdown → restart)
kill -TERM $(cat /tmp/miw_service.lock.pid 2>/dev/null) || true
python3 -m src.market.service.main --dry-run ...
```

Session tokens are read per-request via `EnvSecretProvider`, so a running
service picks up a new value if the env var is updated and the transport
re-acquires it on the next request. No restart is strictly required for the
token; a restart is required to pick up `APP_KEY` or `PINNACLE_API_KEY` changes.

### Telegram bot token
```bash
export TELEGRAM_BOT_TOKEN="new_token"
# Token is read once at build_runtime() time; requires restart.
```

### Audit trail
All requests are logged to the `RequestAuditLog` (secrets are redacted via `***`).
Secret values never appear in logs or SQLite audit tables.

---

## Database Files

| File | Module | Contents |
|---|---|---|
| `scheduler.db` | M10.1 SnapshotScheduler | Scheduled events, observed flags, tick history |
| `truth.db` | M2 TruthStore | Ingested raw snapshots, canonical truth records |
| `bridge.db` | M10.2 IngestionBridge | Job history, liquidity records |
| `control.db` | M9.1 ControlPlane | State transitions, hash-chained audit ledger |
| `control.db.gate` | M9.2 ControlGateway | Suppression ledger, signal gate history |

All files are append-only SQLite. To replay state after a crash:
```python
from src.market.control import ControlGateway, ControlPlane
plane = ControlPlane(db_path="control.db")
gw = ControlGateway(plane, db_path="control.db.gate")
print(gw.replay())       # shows n_gated, n_published, n_suppressed, chain_valid
print(gw.verify_chain()) # True if ledger is intact
```

---

## Operational Safeguards

| Safeguard | Mechanism |
|---|---|
| Single instance | `SingleInstanceLock("/tmp/miw_service.lock")` — second start raises `RuntimeError` |
| Graceful shutdown | `SIGTERM` / `SIGINT` → sets stop flag → completes current iteration → closes DB connections |
| Bounded sleep | Sleep broken into 1 s slices; shutdown flag checked between slices |
| Provider outage → degraded | After `degraded_failure_threshold` consecutive `ProviderError`s, service enters degraded mode: data ingestion skipped, signals skipped, logging increased. Recovers automatically when provider returns. |
| Config validation | `config.validate()` called in `build_runtime()` before any connection is opened |
| Secret redaction | `RequestAuditLog` never records header values; `***` appears in log entries |
| Chain integrity | `ControlGateway.verify_chain()` checked on service startup (in `build_runtime`) |

---

## Health Endpoint

```python
snap = service.health_snapshot()
print(snap.to_dict())
# {
#   "timestamp": "2026-06-11T15:00:00+00:00",
#   "control_state": "PAPER",
#   "risk_index": 0.12,
#   "provider_health": {"betfair": {"success": 14, "failure": 0, ...}},
#   "completeness_score": 0.93,
#   "signals_published": 3,
#   "signals_suppressed": 1,
#   "settlement_lag_seconds": 420.5,
#   "degraded": false,
#   "iteration_count": 72,
# }
```

---

## Acceptance Hash

The M11 offline acceptance hash must remain unchanged across all deploys:

```
ab3844b895a887e3579a29e273261154743507bf157596bc4657aaa7b901abcd
```

Verify:
```bash
python3 -m unittest tests.test_m11_acceptance -v
```
