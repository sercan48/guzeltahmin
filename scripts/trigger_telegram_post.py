import asyncio
import os
from dotenv import load_dotenv
from telegram.ext import Application
from app.bot.predictions import free_daily_pick_job

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
    
    print("Triggering Free Daily Pick Job...")
    try:
        await free_daily_pick_job(mock_context)
        print("Job triggered successfully! Check your Telegram channel.")
    except Exception as e:
        print(f"Error triggering job: {e}")

if __name__ == "__main__":
    asyncio.run(main())
