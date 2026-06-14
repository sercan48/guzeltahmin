"""
One-shot trigger: run the WC shadow bulletin immediately and deliver to personal channel.
Identical logic to the bot's wc_shadow_delivery_job (17:00 TR daily job).

Usage:
    python scripts/trigger_shadow.py               # dry-run
    python scripts/trigger_shadow.py --deliver     # send to TELEGRAM_PERSONAL_CHANNEL
    python scripts/trigger_shadow.py --date 2026-06-14 --deliver
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ops.wc_paper_shadow import main

if __name__ == "__main__":
    raise SystemExit(main())
