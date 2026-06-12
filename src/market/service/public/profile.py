"""PHASE-LIVE L7 — PublicChannelProfile.

Configuration for FREE-channel public operation with teaser-only publication
and dry-run-safe defaults.

Additive: builds on L5 monetization policy. No changes to M1-M11 / L1-L6.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..monetization.models import FORMAT_RULES, FormatType, UserTier


@dataclass
class PublicChannelProfile:
    """FREE-channel public publication configuration.

    Enforces teaser-only delivery: the public channel only ever receives
    content the FREE tier is permitted to see (TEASER / ABBREVIATED), never
    PRO/BASIC full-format content.
    """
    channel_id: str = ""                  # "@miw_free" or "-100xxx"
    enabled: bool = True
    dry_run: bool = True                  # ALWAYS dry-run by default
    teaser_only: bool = True              # never publish FULL format publicly
    publish_high_tier_only: bool = True   # only TIER_S/A teasers (skip B/C noise)
    max_publications_per_day: int = 50    # rate cap for the public channel

    # ------------------------------------------------------------------ #

    def allowed_format_for_grade(self, grade: str) -> str:
        """Return the FormatType the FREE tier receives for a signal grade.

        Always resolves through the L5 FREE-tier FORMAT_RULES — the single
        source of truth — so public formatting can never exceed FREE policy.
        """
        fmt = FORMAT_RULES.get((UserTier.FREE, grade), FormatType.TEASER)
        return fmt.value

    def is_publishable(self, grade: str) -> bool:
        """Whether a signal of this grade should reach the public channel."""
        if not self.enabled:
            return False
        fmt = self.allowed_format_for_grade(grade)
        if self.teaser_only and fmt == FormatType.FULL.value:
            # Defensive: FREE tier never maps to FULL, but guard anyway.
            return False
        if self.publish_high_tier_only:
            return grade in ("TIER_S", "TIER_A")
        return grade in ("TIER_S", "TIER_A", "TIER_B", "TIER_C")

    def validate(self) -> None:
        if self.enabled and not self.dry_run and not self.channel_id:
            raise ValueError("channel_id required when enabled and not dry_run")
        if self.max_publications_per_day <= 0:
            raise ValueError("max_publications_per_day must be > 0")
        if not self.teaser_only:
            raise ValueError(
                "public channel must be teaser_only=True (no full content leakage)"
            )
