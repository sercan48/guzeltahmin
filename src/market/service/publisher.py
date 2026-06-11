"""PHASE-LIVE L3 — Telegram signal publisher.

Pure-stdlib (urllib). No external deps.

Routing:
  tier >= vip_tier_threshold  ->  vip_channel_id
  tier <  vip_tier_threshold  ->  standard_channel_id
  dry_run=True                ->  no HTTP; result.dry_run=True

Suppression: gate.publish=False -> publisher returns immediately (not sent).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

_TIER_ORDER = {"REJECT": 0, "TIER_C": 1, "TIER_B": 2, "TIER_A": 3, "TIER_S": 4}

_MD2_ESCAPE = re.compile(r'([_*\[\]()~`>#+\-=|{}.!\\])')


def _esc(text: str) -> str:
    return _MD2_ESCAPE.sub(r'\\\1', str(text))


@dataclass
class PublishResult:
    signal_id: str
    published: bool
    channel: Optional[str]
    dry_run: bool
    reason: str


class SignalFormatter:
    """Formats a PaperSignal into Telegram MarkdownV2 text."""

    @staticmethod
    def format_vip(signal) -> str:
        lines = [
            f"🔒 *VIP SIGNAL — {_esc(signal.tier)}*",
            f"Match: `{_esc(signal.match_id)}`",
            f"Market / Selection: *{_esc(signal.market)}* › *{_esc(signal.selection)}*",
            f"Edge Score: `{_esc(f'{signal.edge_score:.2%}')}`",
            f"Confidence: `{_esc(f'{signal.confidence:.0%}')}`",
            f"Truth Confidence: `{_esc(f'{signal.truth_confidence:.0%}')}`",
            f"⏰ `{_esc(signal.timestamp)}`",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_standard(signal) -> str:
        lines = [
            f"🎯 *{_esc(signal.tier)}* — {_esc(signal.selection)} \\({_esc(signal.market)}\\)",
            f"Edge: `{_esc(f'{signal.edge_score:.2%}')}` \\| Conf: `{_esc(f'{signal.confidence:.0%}')}`",
            f"Match: `{_esc(signal.match_id)}` \\| {_esc(signal.timestamp)}",
        ]
        return "\n".join(lines)


class TelegramPublisher:
    """Routes gated signals to Telegram channels.

    In dry_run mode all calls are no-ops; PublishResult.dry_run=True and
    published=True (the signal *would* have been sent).
    """

    def __init__(
        self,
        bot_token: str,
        vip_channel_id: str,
        standard_channel_id: str,
        vip_tier_threshold: str = "TIER_A",
        dry_run: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._token = bot_token
        self._vip = vip_channel_id
        self._std = standard_channel_id
        self._threshold = _TIER_ORDER.get(vip_tier_threshold, 3)
        self.dry_run = dry_run
        self._timeout = timeout
        self._fmt = SignalFormatter()

    # ------------------------------------------------------------------ #

    def publish(self, signal, gate) -> PublishResult:
        """Publish a gated signal. Returns immediately (no-op) if not gate.publish."""
        sid = getattr(gate, "signal_id", "")

        if not gate.publish:
            return PublishResult(sid, False, None, self.dry_run, "suppressed")

        tier_rank = _TIER_ORDER.get(getattr(signal, "tier", ""), 0)
        is_vip = tier_rank >= self._threshold
        channel = self._vip if is_vip else self._std

        if not channel:
            return PublishResult(sid, False, channel, self.dry_run, "no_channel_configured")

        text = (self._fmt.format_vip(signal) if is_vip
                else self._fmt.format_standard(signal))

        if self.dry_run:
            return PublishResult(sid, True, channel, True, "dry_run")

        ok = self._send(channel, text)
        return PublishResult(sid, ok, channel, False, "" if ok else "send_failed")

    # ------------------------------------------------------------------ #

    def _send(self, chat_id: str, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text,
                              "parse_mode": "MarkdownV2"}).encode()
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False
