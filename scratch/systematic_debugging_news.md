# Systematic Debugging Log: Daily News Duplication & Formatting Fix

This log documents the systematic debugging process carried out to fix duplicate news posting and improve news bulletin format.

---

## Phase 1: Reproduce

### Issues Reported
1. **Duplication:** The bot sends daily news posts twice consecutively. Similar content appears in two distinct messages in the Telegram channel.
2. **Formatting:** 
   - The news intro text numbers the count (e.g. "Featured 5 news" / "öne çıkan 5 haber").
   - The content should contain between 5 and 10 unique, non-chatter items, without the hardcoded "5" in the intro title.

---

## Phase 2: Isolate

### Process Isolation (Root Cause of Duplication)
We queried active processes on the host machine using PowerShell:
- Found four active Python processes running `app.telegram_bot`:
  - Two running via system Python 3.11 (`ProcessId 10660` and `ProcessId 16312`).
  - Two running via Windows store Python app (`ProcessId 12756` and `ProcessId 9328`).
- **Conclusion:** Multiple duplicate bot instances running in the background were pulling the same jobs queue and scheduling identical daily news tasks simultaneously, resulting in double messages.

### Format Isolation
In `src/features/news_fetcher.py`:
- Gemini prompt was hardcoded to fetch exactly "5 futbol gelişmesini" and print exactly "5 satır".
- The fallback logic returned exactly the top 5 news entries.
- The title headers had hardcoded "öne çıkan 5 güncel gelişme".
- Occasionally, Gemini generated conversational helper text like `🔹 İşte güncel ve önemli futbol gelişmeleri:` which didn't contain news titles.

---

## Phase 3: Understand

### Duplication Root Cause
When multiple bot polling/scheduling processes run concurrently, they trigger the `daily_news_job` in the exact same minute. Because they execute separately, they query the Gemini API independently, bypass the file-based cache before it is written, and post two slightly different news bulletins to the same channel.

### Formatting Root Cause
The codebase was hardcoded to enforce 5 news items. Introductory/conversational chatter from the LLM was not programmatically stripped out if it was prefixed with `🔹` without a bold title tag.

---

## Phase 4: Fix & Verify

### Actions Taken

1. **Process Cleanup:**
   - Terminated all 4 redundant, background Python processes running `app.telegram_bot` to ensure only one master bot scheduler runs.
   - Recommended the user start the bot exactly once (e.g., `.venv\Scripts\python -m app.telegram_bot`).

2. **Formatting & Count Code Fixes (`src/features/news_fetcher.py`):**
   - **Count Update:** Updated prompts to ask Gemini for "5 ila 10 arasında futbol gelişmesini" and return "5-10 satır". Updated fallback RSS selectors to extract up to 8 unique items (`selected_news = raw_news[:8]`).
   - **Header Removal of Number:** Changed headers from `öne çıkan 5 güncel...` to `öne çıkan güncel ve önemli gelişmeler...` (no numbers).
   - **Chatter Filter:** Programmatically filter out conversational introductory lines returned by Gemini:
     ```python
     if "<b>" not in line or "</b>" not in line:
         continue
     ```
     This ensures that only actual news lines (which must contain bold titles like `🔹 <b>[Başlık]:</b>`) are published.

### Verification
- Ran pytest on news fetcher logic (`pytest tests/test_news_fetcher.py`): **Passed (3/3)**.
- Ran news bulletin generation test script (`scripts/test_news_bulletin.py`): **Passed**.
  - Generated exactly 7 unique, beautifully formatted news items.
  - Successfully filtered out the LLM intro text.
  - No number prefix was printed in the header.
