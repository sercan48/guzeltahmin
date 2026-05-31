import feedparser

feeds = [
    "https://www.skysports.com/rss/12040",
    "http://feeds.bbci.co.uk/sport/football/rss.xml",
    "https://www.espn.com/espn/rss/soccer/news",
    "https://rss.haberler.com/rss.asp?kategori=spor"
]

for url in feeds:
    try:
        f = feedparser.parse(url)
        print(f"Feed: {url} -> {len(f.entries)} entries")
        if len(f.entries) > 0:
            print(f"  Example: {f.entries[0].title}")
    except Exception as e:
        print(f"Feed: {url} -> ERROR {e}")
