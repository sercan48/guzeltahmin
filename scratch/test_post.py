import asyncio
import os
import traceback
from dotenv import load_dotenv
from telegram.ext import Application
from app.bot.predictions import free_daily_pick_job, daily_predictions_job

class MockContext:
    def __init__(self, bot):
        self.bot = bot

async def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env")
        return
        
    app = Application.builder().token(token).build()
    mock_context = MockContext(app.bot)
    
    try:
        print("Triggering Premium/VIP Daily Predictions Job...")
        await daily_predictions_job(mock_context)
        print("Triggering Free Daily Pick Job...")
        await free_daily_pick_job(mock_context)
        print("Success!")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
