import asyncio
import os
import sys
from pathlib import Path

# Add root directory to path
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from telegram.ext import Application
from app.bot.predictions import daily_news_job

class MockContext:
    def __init__(self, bot):
        self.bot = bot

async def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env")
        return
        
    print("Connecting to Telegram Bot API...")
    app = Application.builder().token(token).build()
    
    mock_context = MockContext(app.bot)
    
    print("Triggering Daily News Job...")
    try:
        await daily_news_job(mock_context)
        print("Job triggered successfully! Check your Telegram free channel.")
    except Exception as e:
        print(f"Error triggering job: {e}")

if __name__ == "__main__":
    asyncio.run(main())
