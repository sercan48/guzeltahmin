"""Invisible watermark injector using zero-width Unicode characters.

Encodes a delivery_id as base-3 using three zero-width characters appended
to a Telegram message.  The encoding is reversible: decode(inject(text, n))
returns n for all non-negative integers n.

Character mapping (base-3 digits):
  0 → U+200B  ZERO WIDTH SPACE
  1 → U+200C  ZERO WIDTH NON-JOINER
  2 → U+200D  ZERO WIDTH JOINER
  sentinel → U+2060  WORD JOINER  (marks start of watermark block)

The watermark_map is implicit: queue_id (the encoded integer) is the rowid
in delay_queue.db, so audit lookup is: SELECT * FROM delay_queue WHERE id=?.
"""

from __future__ import annotations

from typing import Optional

_DIGITS = ('​', '‌', '‍')  # base-3 digits
_SENTINEL = '⁠'                       # marks start of watermark block
_ZW_SET = frozenset(_DIGITS)


class WatermarkInjector:
    """Encode/decode delivery_id as invisible Unicode appended to message text."""

    # ------------------------------------------------------------------ #

    def encode(self, delivery_id: int) -> str:
        """Return invisible string encoding `delivery_id`."""
        if delivery_id < 0:
            raise ValueError(f"delivery_id must be ≥ 0, got {delivery_id!r}")
        digits: list[int] = []
        n = delivery_id
        if n == 0:
            digits = [0]
        else:
            while n:
                digits.append(n % 3)
                n //= 3
            digits.reverse()
        return _SENTINEL + "".join(_DIGITS[d] for d in digits)

    def decode(self, text: str) -> Optional[int]:
        """Extract delivery_id from watermarked text. Returns None if absent."""
        pos = text.rfind(_SENTINEL)
        if pos == -1:
            return None
        encoded = text[pos + 1:]
        if not encoded or not all(c in _ZW_SET for c in encoded):
            return None
        result = 0
        for c in encoded:
            result = result * 3 + _DIGITS.index(c)
        return result

    def inject(self, text: str, delivery_id: int) -> str:
        """Append invisible watermark to `text`."""
        return text + self.encode(delivery_id)

    def strip(self, text: str) -> str:
        """Remove watermark from text (for display/testing)."""
        pos = text.rfind(_SENTINEL)
        if pos == -1:
            return text
        return text[:pos]
