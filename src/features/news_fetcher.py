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

def are_titles_similar(title1, title2, threshold=70):
    """
    Checks if two news headlines are similar.
    Returns True if similarity score is above threshold (0-100 scale).
    """
    if not title1 or not title2:
        return False
        
    t1 = title1.lower().strip()
    t2 = title2.lower().strip()
    
    # Try rapidfuzz first
    try:
        from rapidfuzz import fuzz
        score = fuzz.token_set_ratio(t1, t2)
        return score >= threshold
    except ImportError:
        # Fallback to Jaccard word-overlap and SequenceMatcher
        words1 = set(t1.split())
        words2 = set(t2.split())
        if not words1 or not words2:
            return False
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        jaccard = (len(intersection) / len(union)) * 100
        
        from difflib import SequenceMatcher
        seq = SequenceMatcher(None, t1, t2).ratio() * 100
        
        return max(jaccard, seq) >= threshold

def is_duplicate(title, existing_titles, threshold=70):
    """
    Checks if a title is similar to any title in a collection of existing titles.
    """
    return any(are_titles_similar(title, ext, threshold) for ext in existing_titles)

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
                "Google Arama (Search Grounding) ile güncel ve önemli 5 ila 10 arasında futbol gelişmesini bul.\n"
                "Format: '🔹 <b>[Başlık]:</b> [Kısa Detay]' (5-10 satır döndür, giriş/açıklama yazma).\n"
                "Yasak: Reklam, clickbait, 'canlı izle', 'saat kaçta', 'bilet' içerikleri.\n"
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
                
    logger.error("All Gemini news generation retries failed. Falling back to RSS news sources...")
    return _generate_rss_fallback_bulletin(posted_news)


def _generate_rss_fallback_bulletin(posted_news) -> str | None:
    """Generate bulletin using RSS feeds as a robust fallback."""
    import feedparser
    logger.info("Generating news bulletin using RSS fallback...")
    
    feeds = [
        "https://rss.haberler.com/rss.asp?kategori=spor",
        "https://www.skysports.com/rss/12040",
        "http://feeds.bbci.co.uk/sport/football/rss.xml",
        "https://www.espn.com/espn/rss/soccer/news"
    ]
    
    raw_news = []
    
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:15]:
                title = entry.title.strip()
                summary = getattr(entry, "summary", "").strip()
                # Clean HTML tags from summary if any
                summary = re.sub(r'<[^>]+>', '', summary)
                
                # Check duplication
                if is_duplicate(title, posted_news):
                    continue
                
                # For Haberler, make sure it's football-related
                if "haberler.com" in url:
                    keywords = ["futbol", "transfer", "mac", "maç", "gol", "lig", "galatasaray", "fenerbahce", "fenerbahçe", "besiktas", "beşiktaş", "trabzonspor", "hatay", "milli takım"]
                    if not any(kw in title.lower() or kw in summary.lower() for kw in keywords):
                        continue
                
                raw_news.append({
                    "title": title,
                    "summary": summary,
                    "source": "Haberler" if "haberler.com" in url else "Sky Sports" if "skysports" in url else "BBC" if "bbci.co.uk" in url else "ESPN"
                })
        except Exception as e:
            logger.warning(f"Failed to parse RSS feed {url}: {e}")
            
    if not raw_news:
        logger.error("No news found in any RSS feeds. Cannot generate bulletin.")
        return None
        
    # Take up to 8 unique news items
    selected_news = raw_news[:8]
    
    # Try to use Gemini to translate and format the RSS items (lightweight prompt, no grounding tool needed)
    if GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            
            articles_text = ""
            for idx, item in enumerate(selected_news, 1):
                articles_text += f"{idx}. [{item['source']}] Title: {item['title']}\nSummary: {item['summary']}\n\n"
                
            prompt = (
                "Aşağıdaki spor haberlerini Türkçe'ye çevir ve özetle. Her haber için tek bir satır oluştur.\n"
                "Format tam olarak şöyle olmalıdır:\n"
                "🔹 <b>[Haber Başlığı]:</b> [Kısa Türkçe Detay]\n"
                "Giriş, açıklama veya yorum yazma, sadece bu haber satırlarını döndür.\n\n"
                f"{articles_text}"
            )
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt
            )
            
            if response.text:
                lines = [l.strip() for l in response.text.split("\n") if l.strip()]
                valid_items = []
                for line in lines:
                    if line.startswith("**") or "**" in line:
                        line = line.replace("**", "<b>", 1).replace("**", "</b>", 1)
                    if not line.startswith("🔹"):
                        line = re.sub(r'^[\d\.\-\*\s]+', '', line).strip()
                        line = f"🔹 {line}"
                        
                    if "<b>" not in line or "</b>" not in line:
                        continue
                        
                    valid_items.append(line)
                    
                    match = re.search(r'<b>(.*?)</b>', line)
                    if match:
                        title_clean = match.group(1).replace(':', '').strip()
                        posted_news.add(title_clean)
                        
                if valid_items:
                    save_posted_news(posted_news)
                    bulletin_content = "\n".join(valid_items)
                    bulletin = "🗞️ <b>Dünya futbolundan öne çıkan güncel gelişmeler (RSS Kaynaklı):</b>\n\n"
                    bulletin += bulletin_content
                    bulletin += "\n"
                    bulletin += generate_dynamic_footer(valid_items)
                    logger.info("RSS news bulletin translated and generated successfully using Gemini.")
                    return bulletin
        except Exception as e:
            logger.warning(f"Gemini failed to translate RSS news: {e}")
            
    # Pure Python template fallback (if Gemini API key is missing or completely failed)
    valid_items = []
    for item in selected_news:
        title = item['title']
        summary = item['summary'][:120] + "..." if len(item['summary']) > 120 else item['summary']
        line = f"🔹 <b>{title}:</b> {summary} (Kaynak: {item['source']})"
        valid_items.append(line)
        posted_news.add(title)
        
    save_posted_news(posted_news)
    bulletin_content = "\n".join(valid_items)
    bulletin = "🗞️ <b>Dünya futbolundan öne çıkan güncel gelişmeler (Yedek RSS Akışı):</b>\n\n"
    bulletin += bulletin_content
    bulletin += "\n"
    bulletin += generate_dynamic_footer(valid_items)
    logger.info("RSS news bulletin generated successfully using Python fallback.")
    return bulletin
