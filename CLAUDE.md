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
