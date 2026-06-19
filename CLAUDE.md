# GüzelTahmin — Proje Hafızası

Bu dosya her Claude Code oturumunda otomatik okunur.
Yeni oturumda bağlamı sıfırdan anlatmana gerek yok.

---

## Projenin Özü

Deterministik Poisson + Elo + GBM hibrit modeli.
Şu an **WC 2026 Shadow (paper-trading) doğrulama fazında**.
Lig tahminleri Ağustos 2025 hedefiyle hazırlanıyor.

---

## Kritik Kısıtlamalar — ASLA İhlal Edilmez

```
calibration_mode = 'identity'   # değiştirme
```

- `ops/result_settler.py` — DOKUNMA (hash korumalı)
- `src/model/wc_intelligence_engine.py` — DOKUNMA (hash korumalı)
- Isotonic regression uygulama
- Olasılık üretimini değiştirme
- Güven skorunu değiştirme
- Poisson/Elo/GBM bileşenlerini değiştirme

**Kural:** Kanıt topla, önce raporla. İzin almadan model değişikliği yapma.

---

## Shadow Fazı Mevcut Durum (WC 2026)

| Metrik | Değer |
|---|---|
| n_settled | 24 |
| Accuracy | 62.5% |
| Brier | 0.600 |
| ECE | 0.308 |
| Draw bias | +17.35pp |

**Aktif flagler:**
- `DRAW_CALIBRATION_CONFIRMED` — bias=+17.35pp (WC fazında beklenen)
- `ECE_REVIEW_REQUIRED` — ECE=0.308 > 0.08 eşiği

**n=30 checkpoint** yaklaşıyor (2 maç daha lazım).

---

## Lig Backtest Sonuçları (5.091 maç, 2 sezon)

| Lig | 24/25 Acc | 25/26 Acc | Draw Bias | Brier |
|---|---|---|---|---|
| Ligue 1 | 57.5% | 50.5% | ≤5pp | 0.606 |
| Eredivisie | 56.9% | 52.3% | ≤1pp | 0.621 |
| Süper Lig | 54.4% | 52.3% | ≤4pp | 0.619 |
| Bundesliga | 50.3% | 54.9% | ≤1pp | 0.615 |
| La Liga | 52.4% | 51.0% | ≤1pp | 0.612 |
| Primeira Liga | 52.9% | 52.9% | ≤2pp | 0.615 |
| Serie A | 51.6% | 49.2% | ≤4pp | 0.624 |
| Premier League | 50.3% | 46.8% | ≤3pp | 0.625 |

Lig modeli WC'ye göre ~5-8pp daha düşük accuracy → beklenen (Club Elo < National Elo kalitesi).

---

## Dosya / Klasör Haritası

```
ops/
  shadow_predictor.py       # WC paper-trading tahmincisi (geçici, kaldırılacak)
  result_settler.py         # Settlement pipeline (KORUNAN)
  wc_paper_shadow.py        # WC bülten üretici
  league_backtest.py        # 8 lig × 2 sezon backtest scripti

src/model/
  wc_intelligence_engine.py # Ana model (KORUNAN)
  summer_league_modifier.py # Yaz ligleri modifier (ertelenmiş)

data/
  shadow_settlements.jsonl  # WC shadow settlement logu
  shadow_accuracy.json      # Güncel doğruluk raporu
  league_backtest/          # 16 backtest JSON çıktısı
  backtest/                 # Ham CSV'ler (8 lig × 2 sezon = 16 dosya)
  cache/club_elo/           # Club Elo ay bazlı önbellek

.github/workflows/
  daily-bulletin.yml        # 21:00 UTC — WC bülten (Telegram)
  daily-settle.yml          # 07:00 UTC — settlement + raporlama

docs/research/              # Makale & akademik kaynak havuzu (aşağıya bak)
```

---

## CSV Dosya Eşleştirmesi (data/backtest/)

| Lig Anahtarı | 24-25 CSV | 25-26 CSV |
|---|---|---|
| PL | `Premiere Lig 24-25.csv` | `Premiere Lig 25-26.csv` |
| LaLiga | `LA Liga 24-25.csv` | `La Liga 25-26.csv` |
| Bundesliga | `Bundesliga 24-25.csv` | `Bundesliga 25-26.csv` |
| SerieA | `Seri A 24-25.csv` | `Seri A 25-26.csv` |
| Ligue1 | `Ligue 24-25.csv` | `Ligue 25-26.csv` |
| Eredivisie | `Eredivisie 24-25.csv` | `Eredivisie 25-26.csv` |
| SuperLig | `Turkey 24-25.csv` | `Turkey 25-26.csv` |
| PrimeiraLiga | `Portugal 24-25.csv` | `Portugal 25-26.csv` |

Kaynak: football-data.co.uk (ücretsiz, tarayıcıdan indir).
Club Elo API bu ortamda 403 veriyor → script statik `_STATIC_ELO` tablosuna fallback yapar.

---

## Lig Komutu Örnekleri

```bash
# Tek lig backtest:
python ops/league_backtest.py --league PL --season 2024 --csv "data/backtest/Premiere Lig 24-25.csv"

# Tüm ligler (API-Football key varsa):
python ops/league_backtest.py --all --season 2024
```

---

## API Anahtarları

| Servis | Env Var | Durum |
|---|---|---|
| The Odds API | `ODDS_API_KEY` | Aktif (WC bülteni için) |
| API-Football | `API_FOOTBALL_KEY` | Yok (lig canlı fixture için gerekli) |
| Telegram Bot | `TELEGRAM_BOT_TOKEN` | Aktif |
| Telegram Chat | `TELEGRAM_CHAT_ID` | Aktif |

---

## Aktif PR

- **PR #8** — `claude/cool-ramanujan-bbckt5` branch'i
  - league_backtest.py (LB-1)
  - 16 backtest çıktısı
  - Auto-detect football-data.co.uk CSV formatı
  - Statik Club Elo tablosu (8 lig, 200+ kulüp)

---

## Yaz Ligleri (Ertelenmiş)

`src/model/summer_league_modifier.py` mevcut. 5 lig planlandı:
MLS, Brazil Série A, Norway Eliteserien, Sweden Allsvenskan, Japan J1.
Club Elo kapsamı yetersiz → Q4 2025'e ertelendi.

---

## Roadmap Özeti

| Faz | Süre | Durum |
|---|---|---|
| Backtest altyapısı | Haziran | ✅ Tamamlandı |
| Backtest koşusu (8 lig × 2 sezon) | Haziran | ✅ Tamamlandı |
| Dixon-Coles draw düzeltmesi R&D | Temmuz | Planlandı |
| API kararı (API-Football vs football-data.org) | Temmuz | Bekliyor |
| Lig fixture pipeline | Ağustos | Planlandı |
| Lig shadow başlangıcı | Ağustos | Planlandı |
| Canlı yayın (VIP Telegram) | Eylül | Planlandı |

---

## Araştırma Bulguları — Modele Yansımaları

Kaynak: `docs/research/football_data_betting_articles_ALL.txt`
(football-data.co.uk makale serisi — Joseph Buchdahl)

### 1. CLV (Closing Line Value) — En Kritik Bulgu

**Ne diyor:** Bir modelin gerçek becerisi, tahmin doğruluğundan değil,
tahmin edilen olasılıkların **Pinnacle kapanış oranını yenip yenmediğiyle** ölçülür.
CLV zamanla kalıcıdır (varyansın %50'sini açıklar); kâr/zarar tamamen geri ortalamasına döner.

**Projeye etkisi:**
- Shadow fazında sadece doğruluk takip etmek yetmez
- `data/shadow_settlements.jsonl`'a `closing_odds_pinnacle` alanı eklenecek
- Her tahmin için `clv = predicted_prob - (1/closing_odds)` hesaplanacak
- Pozitif CLV ortalaması → gerçek edge kanıtı

### 2. Hot Hand Fallacy — Direkt Uygulanabilir Sistem

**Ne diyor:** Son 6 maçta "sıcak" takımlara (galibiyet serisi) karşı
"soğuk" takımlar (%105.48 ROI vs %95.83) istatistiksel olarak anlamlı üstünlük sağlar.
(p-value = 0.00009). Bahisçiler seri gören takıma oranı mantıksız kısaltır.

**Projeye etkisi:**
- `form_score` hesaplamasına **ters kontraryanlık ağırlığı** eklenecek
- Yüksek form_score'lu favori → hafif olasılık indirimi
- Düşük form_score'lu underdog → hafif olasılık artışı
- Dixon-Coles R&D'sine paralel olarak test edilecek

### 3. İstatistiksel Anlamlılık Eşikleri

**Ne diyor:** 10% yield ile even-money tahminlerde n≥540 gerekir.
Uzun odds'larda (9/1) n≥540 ve %34 yield lazım.
n=24 hiçbir istatistiksel anlam taşımaz.

**Projeye etkisi:**
- Shadow checkpoint'leri: n=30 (gözlem), n=100 (ilk sinyal), n=500 (güven)
- VIP lansmanı için minimum n=100 (yield %10+ ise) veya n=500 (yield %3-5)
- Raporlarda "istatistiksel olarak anlamlı değil" notu eklenmeli

### 4. Favori-Uzun İhtimal Sapması (Favourite-Longshot Bias)

**Ne diyor:** Bookmakerlar kısa favorilere düşük marj, uzun ihtimallere yüksek marj uygular.
Bu rasyonel risk yönetimidir (Kelly kriteri ile açıklanır).
Pratik etki: longshot'lara oranlar gerçek olasılıktan kısa, favorilere uzun.

**Projeye etkisi:**
- Tahmin olasılıklarını oranlara çevirirken marjı dağıtırken bu eğriliği dikkate al
- Güçlü favori tahminlerimiz gerçekte piyasadan daha değerli olabilir

### 5. Staking — Half-Kelly Önerisi

**Ne diyor:** Kelly optimal büyüme sağlar ama edge tahminindeki hata
bankrolü mahvedebilir. Half-Kelly: kâr olasılığını %66'dan %73'e çıkarır,
banka yarılanma riskini %10'dan %1'e düşürür.

**Projeye etkisi:**
- VIP kullanıcılara **Half-Kelly** önerilecek (tam Kelly değil)
- Edge tahmini = `(predicted_prob × decimal_odds - 1)`
- Kelly fraction = `edge / (decimal_odds - 1)`
- Önerilen stake = `bankroll × 0.5 × kelly_fraction`

### 6. Martingale/Progresif Staking — Kesinlikle Önerme

**Ne diyor:** Progresif bahis matematiksel olarak yıkıcıdır.
1000× başlangıç bankrolü ile 365 bahiste yıkım kaçınılmazdır.

**Projeye etkisi:**
- Telegram bültenlerinde ve VIP kanalda progresif sistemler hiçbir zaman önerilmeyecek

### 7. Piyasa Verimliliği — Pinnacle Kapanış = Altın Standart

**Ne diyor:** Pinnacle kapanış oranları ile gerçek sonuçlar arasında
r=0.995 korelasyon var (52,411 maç). Tahminlerimizi Pinnacle kapanışa göre ölçmeliyiz.

**Projeye etkisi:**
- `ODDS_API_KEY` ile çekilen oranlar arasında Pinnacle öncelikli
- Bulletin'e "Pinnacle kapanış" kıyaslaması eklenecek

---

## Akademik Kaynak Havuzu

`docs/research/` klasörüne makale ekleyebilirsin.
Her oturumda bu klasörü `ls docs/research/` ile kontrol et.

### Desteklenen formatlar
- `.md` / `.txt` — direkt okunur
- `.pdf` — Read tool ile okunur
- `.html` — WebFetch ile okunur (URL olarak ekle)

### Nasıl eklersin?
1. Dosyayı `docs/research/` klasörüne koy
2. `git add docs/research/ && git push origin main` ile gönder
3. Claude sonraki oturumda `docs/research/` klasörünü tarar ve okur

### Mevcut kaynaklar
<!-- Makale ekledikçe burası güncellenecek -->
*(henüz kaynak yok)*

---

## Geliştirme Dalı

Ana branch: `claude/cool-ramanujan-bbckt5`
Tüm değişiklikler bu branch'e push edilir.
