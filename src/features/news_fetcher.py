"""
Football News Automation logic for Telegram VIP Funnel.
Fetches and formats real-time news using Gemini Google Search Grounding.
"""
import logging
import random
import json
import time
import re
from pathlib import Path
from config.settings import CACHE_DIR, GEMINI_API_KEY, TELEGRAM_VIP_LINK

logger = logging.getLogger(__name__)

POSTED_NEWS_FILE = CACHE_DIR / "posted_news.json"

def load_posted_news():
    if POSTED_NEWS_FILE.exists():
        try:
            with open(POSTED_NEWS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_posted_news(news_set):
    try:
        with open(POSTED_NEWS_FILE, "w", encoding="utf-8") as f:
            # Keep only last 100 to prevent file bloat
            json.dump(list(news_set)[-100:], f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to save posted news: {e}")



def generate_dynamic_footer(news_items):
    """
    Generates a dynamic algorithm note and call-to-action to prevent repetition.
    """
    # Check if there are any injuries/suspensions mentioned in the current news items
    has_injury = any(any(kw in item.lower() for kw in ["sakat", "injury", "injured"]) for item in news_items)
    
    if has_injury:
        footer_note = (
            "🤖 <b>Algoritma Notu:</b> Bültendeki kritik sakatlık/kadro dışı gelişmeleri Monte Carlo simülasyonumuza "
            "işlenerek takım güç dengeleri güncellenmiştir. Eksiklerin bahis oranları üzerindeki etkisi hesaplanmıştır."
        )
        footer_cta = f"💎 Sakatlıkların oranları nasıl etkilediğini görmek ve güncel VIP slipimizi almak için VIP kanalımıza davetlisiniz: {TELEGRAM_VIP_LINK}"
        return f"\n{footer_note}\n\n{footer_cta}"
        
    # General pool of distinct footers
    variations = [
        {
            "note": "🤖 <b>Algoritma Notu:</b> Bu gelişmelerin ardından bahis piyasalarında (Market Delta) ani hareketlilikler gözlemlendi. XGBoost tabanlı tahmin modelimiz yeni olasılıkları hesapladı.",
            "cta": f"💎 Detaylı oran analizleri ve güncel tahmin slipi VIP kanalımızda yayında! VIP Giriş: {TELEGRAM_VIP_LINK}"
        },
        {
            "note": "🤖 <b>Algoritma Analizi:</b> Son dakika haberleri doğrultusunda bugünün fikstüründeki Güven Puanları (Confidence Score) güncellendi. Modelimiz yapay zeka konsensüsünü yeniden hesapladı.",
            "cta": f"💎 Risk analizi yapılmış resmi kuponlar ve VIP tahminlerimiz için VIP kanalımıza katılın: {TELEGRAM_VIP_LINK}"
        },
        {
            "note": "🤖 <b>Yapay Zeka Raporu:</b> Son takım verileri doğrultusunda Ensemble modelimiz tüm pazarları tarayarak (Omni-Market) değer kaymalarını ayıkladı.",
            "cta": f"💎 Akıllı filtrelerden geçen sistem analizleri ve VIP tahmin slipimiz için hemen VIP kanalımıza katılın: {TELEGRAM_VIP_LINK}"
        },
        {
            "note": "🤖 <b>Algoritma Bildirimi:</b> Takım güçlerindeki son değişimler ve xG tahmin oranları yapay zeka tarafından güncellenmiştir.",
            "cta": f"💎 Güncel oranlar ve VIP analizler için VIP kanalımıza katılım sağlayın: {TELEGRAM_VIP_LINK}"
        }
    ]
    
    selected = random.choice(variations)
    return f"\n{selected['note']}\n\n{selected['cta']}"

def generate_news_bulletin():
    """Generates the Telegram HTML message using Gemini Google Search Grounding.
    Retries with delays on failure. Returns None if all retries fail.
    """
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY is not configured. Cannot generate news bulletin.")
        return None
        
    posted_news = load_posted_news()
    max_retries = 3
    retry_delay = 10 # seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            # Format previously shared news context compactly (limit to last 10 to save tokens)
            previously_shared_context = ", ".join(list(posted_news)[-10:])
            
            prompt = (
                "Google Search ile son dakika önemli futbol gelişmelerini (transferler, sakatlıklar, resmi kararlar, kritik maç sonuçları) bul.\n"
                "Sadece en güncel ve gerçek 5-6 haberi Türkçe olarak şu formatta yaz:\n"
                "🔹 <b>[Konu/Başlık]:</b> [Bir spor editörü kalitesinde yazılmış, profesyonel, akıcı, açıklayıcı ve bilgilendirici 2-3 cümlelik detay]\n"
                "Yasak: Giriş/açıklama metni yazma, clickbait, reklam, yayın saati, bilet veya canlı izleme linki.\n"
                f"Tekrar etme: {previously_shared_context}"
            )
            
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=config
            )
            
            if response.text:
                lines = [l.strip() for l in response.text.split("\n") if l.strip()]
                valid_news_items = []
                for line in lines:
                    # Replace markdown bold with HTML bold
                    if line.startswith("**") or "**" in line:
                        line = line.replace("**", "<b>", 1).replace("**", "</b>", 1)
                    
                    if not line.startswith("🔹"):
                        line = re.sub(r'^[\d\.\-\*\s]+', '', line).strip()
                        line = f"🔹 {line}"
                        
                    if "<b>" not in line or "</b>" not in line:
                        continue
                        
                    valid_news_items.append(line)
                    
                    # Extract title and add to posted news cache
                    match = re.search(r'<b>(.*?)</b>', line)
                    if match:
                        title_clean = match.group(1).replace(':', '').strip()
                        posted_news.add(title_clean)
                
                if valid_news_items:
                    save_posted_news(posted_news)
                    bulletin_content = "\n".join(valid_news_items)
                    
                    # Assemble final bulletin
                    bulletin = "🗞️ <b>Dünya futbolundan öne çıkan güncel ve önemli gelişmeler şu şekilde:</b>\n\n"
                    bulletin += bulletin_content
                    bulletin += "\n"
                    bulletin += generate_dynamic_footer(valid_news_items)
                    
                    logger.info("News bulletin generated successfully using Gemini Google Search Grounding.")
                    return bulletin
                    
        except Exception as e:
            logger.warning(f"Gemini news generation attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
                
    logger.error("All Gemini news generation retries failed. No fallback available.")
    return None
