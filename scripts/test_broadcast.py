import asyncio
import os
from dotenv import load_dotenv
from telegram.ext import Application

async def main():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    free_channel = os.getenv("TELEGRAM_FREE_CHANNEL_ID")
    vip_channel = os.getenv("TELEGRAM_CHANNEL_ID")
    
    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env")
        return
        
    print("Connecting to Telegram Bot API...")
    app = Application.builder().token(token).build()
    
    # Initialize the application to ensure it can send messages
    await app.initialize()
    
    test_msg_free = (
        "🚀 <b>GÜZEL TAHMİN SİSTEM TESTİ</b> 🚀\n\n"
        "Merhabalar! Güzel Tahmin yapay zeka botu başarıyla "
        "ücretsiz kanalımıza bağlanmıştır.\n\n"
        "<i>Her gün saat 10:30'da günün ücretsiz tekli tahmini bu kanalda olacaktır.</i>"
    )
    
    test_msg_vip = (
        "💎 <b>GÜZEL TAHMİN VIP SİSTEM TESTİ</b> 💎\n\n"
        "Merhabalar! Güzel Tahmin yapay zeka botu başarıyla "
        "Premium kanalımıza bağlanmıştır.\n\n"
        "<i>Sistem güvencesindeki tüm tahminler, canlı analizler ve "
        "özel turnuva modülleri burada paylaşılacaktır.</i>"
    )
    
    try:
        print(f"Sending message to FREE channel: {free_channel}")
        await app.bot.send_message(chat_id=free_channel, text=test_msg_free, parse_mode="HTML")
        print("[OK] Free channel message sent successfully!")
    except Exception as e:
        print(f"[FAIL] Failed to send to FREE channel: {e}")
        
    try:
        print(f"Sending message to VIP channel: {vip_channel}")
        await app.bot.send_message(chat_id=vip_channel, text=test_msg_vip, parse_mode="HTML")
        print("[OK] VIP channel message sent successfully!")
    except Exception as e:
        print(f"[FAIL] Failed to send to VIP channel: {e}")

if __name__ == "__main__":
    asyncio.run(main())
