# PHASE-LIVE L7 — Public Dry-Run Launch

Operate the system publicly with **FREE-channel teaser delivery** while
collecting real-world operational metrics. Default mode is dry-run; no real
Telegram messages are sent unless `dry_run=False` and a `send_fn` is injected.

## What L7 adds (all additive)

| File | Component |
|---|---|
| `src/market/service/public/profile.py` | `PublicChannelProfile` — FREE-channel config, teaser-only rules |
| `src/market/service/public/metrics.py` | `PublicDeliveryMetrics`, `SubscriberTracker` |
| `src/market/service/public/dashboard.py` | `DashboardExporter` — daily JSON + trend series |
| `src/market/service/public/launch.py` | `PublicPublisher`, `LaunchValidationHarness`, `build_public_launch()` |

No changes to M1–M11 or L1–L6 behaviour.

## Three-layer leakage protection

Full-format (PRO/BASIC) content can never reach the public channel. Three
independent layers enforce this:

1. **Grade gating** — `PublicChannelProfile.is_publishable(grade)`
2. **Format resolution** — `allowed_format_for_grade()` resolves through the
   L5 **FREE-tier** `FORMAT_RULES` only (FREE never maps to `FULL`)
3. **Content inspection** — `assert_no_leakage(text)` raises `LeakageError` if
   any of `"Edge Score"`, `"Truth Confidence"`, `"VIP SIGNAL"` appear in the
   rendered text, before any send

## Quick start

```python
from src.market.service.public import build_public_launch, PublicChannelProfile

profile = PublicChannelProfile(
    channel_id="@miw_free",
    dry_run=True,                  # no real sends
    teaser_only=True,              # enforced (validate() rejects False)
    publish_high_tier_only=True,   # only TIER_S/A teasers
    max_publications_per_day=50,
)
harness, comps = build_public_launch(profile, signal_source=my_allow_signals)
harness.run(poll_interval_seconds=30.0)   # blocks; SIGTERM for shutdown
```

## Metrics collected

**Public delivery** (`PublicDeliveryMetrics`): impressions, delivered,
teasers, abbreviated, suppressed-non-public, publication latency p50/p95,
delivery rate.

**Subscribers** (`SubscriberTracker`): total free, active free (engaged within
window), total engagements, unique engaged, engagement rate.

## Dashboard export

`harness.export_dashboard(day, readiness, operational, alerts)` writes
`<report_dir>/public-<YYYY-MM-DD>.json` with `mode: "PUBLIC_DRY_RUN"`, current
delivery/subscriber metrics, and rolling 90-day trend series for readiness,
provider health, and signal volume.

## Validation

`harness.verify_publication_rules(sample_signals)` returns
`{checked, violations, passed}` — `passed=False` if any publishable grade
resolves to FULL or any rendered text fails the leakage guard.

## Definition of Done — verification

```bash
python3 -m unittest tests.test_service_public -v   # 63 offline tests
python3 -m unittest tests.test_m11_acceptance -v   # acceptance hash unchanged
```

Acceptance hash: `ab3844b895a887e3579a29e273261154743507bf157596bc4657aaa7b901abcd`
