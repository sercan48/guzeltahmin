"""TieredDeliveryWorker — polls delay queue and delivers due signals.

Delivery path:
  DelayScheduler.due() → reconstruct signal → format per FormatType
  → WatermarkInjector.inject(text, queue_id) → publisher._send(channel_id, text)
  → mark_delivered() + write to delivery_log

delivery_log.db schema (append-only):
  delivery_log — one row per delivery attempt

Uses publisher.dry_run to skip HTTP; publisher._send() for live sends.
No modifications to TelegramPublisher.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from types import SimpleNamespace
from typing import Optional

from ..publisher import SignalFormatter
from .clock import Clock
from .delay import DelayScheduler
from .models import DeliveryRecord, FormatType, QueueEntry
from .store import UserStore
from .watermark import WatermarkInjector


_DDL_DELIVERY = """
CREATE TABLE IF NOT EXISTS delivery_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id        INTEGER NOT NULL,
    signal_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    user_tier       TEXT NOT NULL,
    channel_id      TEXT NOT NULL,
    format_type     TEXT NOT NULL,
    published       INTEGER NOT NULL,
    delivered_at_ts REAL NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);
"""

_SIGNAL_FIELDS = (
    "match_id", "market", "selection", "edge_score", "tier",
    "confidence", "truth_confidence", "timestamp",
)

_MD2_ESCAPE = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def _esc(text: str) -> str:
    return _MD2_ESCAPE.sub(r'\\\1', str(text))


def _serialize_signal(signal) -> str:
    d = {f: getattr(signal, f, None) for f in _SIGNAL_FIELDS}
    return json.dumps(d, default=str)


def _deserialize_signal(json_str: str) -> SimpleNamespace:
    return SimpleNamespace(**json.loads(json_str))


def _serialize_gate(gate) -> str:
    d = {
        "signal_id": getattr(gate, "signal_id", ""),
        "state":     str(getattr(gate, "state", "")),
        "decision":  str(getattr(gate, "decision", "")),
        "publish":   bool(getattr(gate, "publish", False)),
        "recorded":  bool(getattr(gate, "recorded", False)),
        "reason_codes": list(getattr(gate, "reason_codes", []) or []),
    }
    return json.dumps(d, default=str)


# ---------------------------------------------------------------------------
# Signal text formatter (format-type aware)
# ---------------------------------------------------------------------------

_shared_fmt = SignalFormatter()


def _format_signal(signal: SimpleNamespace, format_type: str) -> str:
    if format_type == FormatType.FULL.value:
        tier_rank = {"REJECT": 0, "TIER_C": 1, "TIER_B": 2, "TIER_A": 3, "TIER_S": 4}
        if tier_rank.get(getattr(signal, "tier", ""), 0) >= 3:
            return _shared_fmt.format_vip(signal)
        return _shared_fmt.format_standard(signal)
    if format_type == FormatType.ABBREVIATED.value:
        return _shared_fmt.format_standard(signal)
    # TEASER
    tier   = _esc(getattr(signal, "tier", ""))
    market = _esc(getattr(signal, "market", ""))
    return (
        f"⚡ *High\\-confidence signal identified*\n"
        f"Grade: `{tier}` \\| Market: `{market}`\n"
        f"_Upgrade to PRO to receive this signal in real\\-time\\._"
    )


# ---------------------------------------------------------------------------
# Delivery worker
# ---------------------------------------------------------------------------

class TieredDeliveryWorker:
    """Polls delay queue and delivers due signals via TelegramPublisher._send."""

    def __init__(
        self,
        delay_scheduler: DelayScheduler,
        watermark_injector: WatermarkInjector,
        delivery_db_path: str,
        clock: Clock,
    ) -> None:
        self._scheduler = delay_scheduler
        self._wm = watermark_injector
        self._clock = clock
        self._log = logging.getLogger("miw.monetization.worker")

        self._dconn = sqlite3.connect(delivery_db_path, check_same_thread=False)
        self._dconn.row_factory = sqlite3.Row
        self._dconn.execute(_DDL_DELIVERY)
        self._dconn.commit()

    # ------------------------------------------------------------------ #

    def deliver_due(self, publisher) -> int:
        """Deliver all queued entries past their publish_after. Returns count."""
        due = self._scheduler.due(self._clock.now_ts())
        count = 0
        for entry in due:
            try:
                ok = self._deliver_one(publisher, entry)
                if ok:
                    count += 1
            except Exception as exc:
                self._log.warning(
                    "delivery failed queue_id=%d: %s", entry.queue_id, exc
                )
        return count

    def delivery_log_count(self) -> int:
        row = self._dconn.execute("SELECT COUNT(*) FROM delivery_log").fetchone()
        return row[0]

    def close(self) -> None:
        self._dconn.close()

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _deliver_one(self, publisher, entry: QueueEntry) -> bool:
        signal   = _deserialize_signal(entry.signal_json)
        text     = _format_signal(signal, entry.format_type)
        marked   = self._wm.inject(text, entry.queue_id)
        now_ts   = self._clock.now_ts()

        if getattr(publisher, "dry_run", True):
            published = True
            reason    = "dry_run"
        else:
            published = bool(publisher._send(entry.channel_id, marked))
            reason    = "" if published else "send_failed"

        self._scheduler.mark_delivered(entry.queue_id, now_ts)
        self._write_delivery_log(entry, published, now_ts, reason)
        return published

    def _write_delivery_log(
        self,
        entry: QueueEntry,
        published: bool,
        now_ts: float,
        reason: str,
    ) -> None:
        with self._dconn:
            self._dconn.execute(
                """INSERT INTO delivery_log
                   (queue_id, signal_id, user_id, user_tier, channel_id,
                    format_type, published, delivered_at_ts, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry.queue_id, entry.signal_id, entry.user_id,
                    entry.user_tier, entry.channel_id, entry.format_type,
                    int(published), now_ts, reason,
                ),
            )
