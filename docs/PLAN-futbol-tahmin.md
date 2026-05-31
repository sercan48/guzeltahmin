# 🎯 PLAN: Güzel Tahmin — Futbol Tahmin & Value Bet Motoru

> **Proje Amacı:** Geçmiş veri, oyuncu nitelikleri ve dış çarpanları birleştirerek maç sonuçlarını tahmin eden, bahis oranlarıyla karşılaştırarak "Değerli Bahis" fırsatlarını bulan bir motor.
> **Hedef Doğruluk:** ≥ %85 (baseline), ROI-pozitif Value Bet tespiti

---

## 📋 Kesinleşen Kararlar

| Karar | Değer |
|-------|-------|
| **Ligler (MVP)** | Süper Lig + Top 10 Avrupa Ligi |
| **Veri Kaynağı (Omurga)** | Football-Data.co.uk CSV (son 5 sezon) |
| **Canlı Veri** | API-Football (Faz 2 — hesap açılacak) |
| **Oyuncu Verileri** | Kaggle FIFA CSV dataset |
| **Hava Durumu** | OpenWeatherMap API |
| **Veritabanı** | SQLite (lokal geliştirme) + Supabase (taşınabilir prod) |
| **Arayüz (MVP)** | Streamlit Desktop App |
| **Arayüz (Faz 2)** | Telegram Bot entegrasyonu |
| **Algoritma** | XGBoost + %40 Sezon / %60 Son 5 Maç dinamik ağırlık |
| **Kullanım** | Bireysel, ama satış/paylaşıma hazır mimari |

---

## 🏗️ Mimari (Architecture)

```
güzel-tahmin/
├── config/
│   ├── settings.py              # Global ayarlar, API keys, paths
│   ├── leagues.py               # Lig tanımları, Football-Data kodları
│   └── constants.py             # Sabit değerler, ağırlık katsayıları
│
├── data/
│   ├── raw/                     # Football-Data.co.uk CSV'leri
│   │   ├── TR1/                 # Süper Lig
│   │   ├── E0/                  # Premier League
│   │   ├── SP1/                 # La Liga
│   │   └── ...                  # Diğer ligler
│   ├── fifa/                    # Kaggle FIFA attribute CSV'leri
│   └── processed/               # Temizlenmiş, birleştirilmiş veriler
│
├── src/
│   ├── __init__.py
│   ├── ingestion/               # Modül 1: Veri Yükleme
│   │   ├── __init__.py
│   │   ├── csv_loader.py        # Football-Data CSV parser
│   │   ├── fifa_loader.py       # FIFA attribute loader
│   │   ├── api_football.py      # [Faz 2] Canlı veri çekme
│   │   ├── weather_client.py    # [Faz 2] OpenWeatherMap client
│   │   └── fuzzy_matcher.py     # İsim eşleştirme (takım/oyuncu)
│   │
│   ├── preprocessing/           # Modül 2: Veri Temizleme
│   │   ├── __init__.py
│   │   ├── cleaner.py           # Eksik veri, outlier temizleme
│   │   ├── normalizer.py        # İstatistik normalizasyonu
│   │   └── schema_mapper.py     # Farklı kaynakları ortak şemaya map
│   │
│   ├── features/                # Modül 3: Feature Engineering
│   │   ├── __init__.py
│   │   ├── team_strength.py     # 40/60 ağırlıklı takım gücü
│   │   ├── form_calculator.py   # Son 5 maç formu + SOS
│   │   ├── player_impact.py     # Mevki bazlı oyuncu etkisi
│   │   ├── chemistry_matrix.py  # Oyuncu çifti uyum katsayısı
│   │   ├── efficiency_engine.py # Piyasa değeri vs performans
│   │   ├── referee_impact.py    # Hakem kart istatistikleri
│   │   ├── weather_multiplier.py# [Faz 2] Hava durumu çarpanı
│   │   └── fatigue_factor.py    # [Faz 2] UEFA/rotasyon yorgunluğu
│   │
│   ├── model/                   # Modül 4: Tahmin Motoru
│   │   ├── __init__.py
│   │   ├── trainer.py           # XGBoost eğitim pipeline
│   │   ├── predictor.py         # Maç tahmini (olasılık)
│   │   ├── confidence_score.py  # 0-100 güven endeksi
│   │   └── hypertuner.py        # Optuna ile hyperparameter tuning
│   │
│   ├── evaluator/               # Modül 5: Değerlendirme & Value Bet
│   │   ├── __init__.py
│   │   ├── value_hunter.py      # Model oranı vs piyasa oranı
│   │   ├── bankometer.py        # Banko maç filtresi
│   │   ├── backtester.py        # Geçmiş performans analizi
│   │   └── confusion_analyzer.py# Hata analizi (lig/oran bazlı)
│   │
│   └── db/                      # Modül 6: Veritabanı
│       ├── __init__.py
│       ├── base.py              # Abstract DB interface
│       ├── sqlite_backend.py    # SQLite implementasyonu
│       ├── supabase_backend.py  # Supabase implementasyonu
│       └── migrations.py        # Schema oluşturma/güncelleme
│
├── app/                         # Arayüz
│   ├── streamlit_app.py         # Streamlit dashboard (MVP)
│   ├── pages/
│   │   ├── dashboard.py         # Ana tahmin paneli
│   │   ├── value_bets.py        # Değerli bahis listesi
│   │   ├── model_stats.py       # Model performans metrikleri
│   │   └── league_explorer.py   # Lig bazlı istatistikler
│   └── telegram_bot.py          # [Faz 2] Telegram bot
│
├── tests/
│   ├── test_ingestion.py
│   ├── test_features.py
│   ├── test_model.py
│   └── test_evaluator.py
│
├── scripts/
│   ├── download_data.py         # Football-Data CSV otomatik indirme
│   ├── init_db.py               # Veritabanı ilk kurulum
│   └── run_backtest.py          # Tam backtest çalıştırma
│
├── docs/
│   └── PLAN-futbol-tahmin.md    # Bu dosya
│
├── requirements.txt
├── .env.example                 # API key şablonu
└── README.md
```

---

## 🗄️ Veritabanı Şeması

### `teams` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | Master team ID |
| name | TEXT | Standart isim |
| aliases | JSON | Alternatif isimler (fuzzy match) |
| league_code | TEXT | Football-Data lig kodu (E0, SP1, TR1...) |
| country | TEXT | Ülke |
| style_score | FLOAT | Teknik (0) → Fiziksel (1) spektrumu |
| created_at | TIMESTAMP | |

### `players` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | Master player ID |
| name | TEXT | Standart isim |
| team_id | FK → teams | Mevcut takım |
| position | ENUM | GK / DEF / MID / FWD |
| fifa_overall | INTEGER | FIFA genel puan (0-99) |
| fifa_pace | INTEGER | Hız |
| fifa_shooting | INTEGER | Şut |
| fifa_passing | INTEGER | Pas |
| fifa_dribbling | INTEGER | Dribling |
| fifa_defending | INTEGER | Defans |
| fifa_physical | INTEGER | Fizik |
| market_value | FLOAT | Piyasa değeri (M euro) |
| importance_score | FLOAT | Takım için vazgeçilmezlik skoru |

### `matches` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | |
| date | DATE | Maç tarihi |
| league_code | TEXT | Lig kodu |
| season | TEXT | "2024-2025" |
| home_team_id | FK → teams | Ev sahibi |
| away_team_id | FK → teams | Deplasman |
| ft_home_goals | INTEGER | FT ev sahibi gol |
| ft_away_goals | INTEGER | FT deplasman gol |
| ft_result | CHAR(1) | H / D / A |
| ht_home_goals | INTEGER | HT ev sahibi gol |
| ht_away_goals | INTEGER | HT deplasman gol |
| home_shots | INTEGER | |
| away_shots | INTEGER | |
| home_shots_target | INTEGER | |
| away_shots_target | INTEGER | |
| home_corners | INTEGER | |
| away_corners | INTEGER | |
| home_fouls | INTEGER | |
| away_fouls | INTEGER | |
| home_yellows | INTEGER | |
| away_yellows | INTEGER | |
| home_reds | INTEGER | |
| away_reds | INTEGER | |
| referee | TEXT | Hakem adı |

### `odds` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | |
| match_id | FK → matches | |
| bookmaker | TEXT | Bet365, Pinnacle vs. |
| home_odds | FLOAT | Ev sahibi oranı |
| draw_odds | FLOAT | Beraberlik oranı |
| away_odds | FLOAT | Deplasman oranı |
| over25_odds | FLOAT | Üst 2.5 oranı |
| under25_odds | FLOAT | Alt 2.5 oranı |

### `predictions` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | |
| match_id | FK → matches | |
| home_win_prob | FLOAT | Model ev sahibi olasılığı |
| draw_prob | FLOAT | Model beraberlik olasılığı |
| away_win_prob | FLOAT | Model deplasman olasılığı |
| confidence_score | INTEGER | 0-100 güven skoru |
| is_value_bet | BOOLEAN | Değerli bahis mi? |
| value_margin | FLOAT | Değer marjı (%) |
| predicted_result | CHAR(1) | H / D / A |
| actual_result | CHAR(1) | Gerçek sonuç (backtest) |
| created_at | TIMESTAMP | |

### `referees` Tablosu
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| id | INTEGER PK | |
| name | TEXT | |
| league_code | TEXT | |
| avg_yellows | FLOAT | Maç başı ortalama sarı |
| avg_reds | FLOAT | Maç başı ortalama kırmızı |
| avg_fouls | FLOAT | Maç başı ortalama faul |
| strictness_score | FLOAT | 0-1 sertlik skoru |

### `chemistry` Tablosu (Faz 2)
| Kolon | Tip | Açıklama |
|-------|-----|----------|
| player1_id | FK → players | |
| player2_id | FK → players | |
| minutes_together | INTEGER | Birlikte oynanan dakika |
| chemistry_score | FLOAT | Uyum katsayısı |

---

## 📐 Algoritma Detayları

### 1. Takım Gücü Hesaplama (Team Strength)

```
TeamStrength = (SeasonAvg x 0.40) + (Last5Form x 0.60)

SeasonAvg = toplam(mac_puanlari) / mac_sayisi  (normalize 0-1)

Last5Form = toplam(son_5_puan x SOS_katsayisi) / 5
  SOS (Strength of Schedule) = rakiplerin lig siralamasi ortalamasi / lig_takim_sayisi
```

**Time Decay (Zamanla Azalan Etki):**
```
decay_weight = e^(-lambda x days_since_match)
lambda = 0.01  (yaklasik 70 gun yari omur)
```

### 2. Mevki Bazlı Oyuncu Etkisi

| Mevki | Birincil Metrik | İkincil Metrik | Ağırlık |
|-------|-----------------|----------------|---------|
| GK | fifa_defending x 0.4 | fifa_physical x 0.3 + reflexes x 0.3 | 0.12 |
| DEF | fifa_defending x 0.5 | fifa_physical x 0.3 + fifa_pace x 0.2 | 0.25 |
| MID | fifa_passing x 0.4 | fifa_dribbling x 0.3 + fifa_shooting x 0.3 | 0.35 |
| FWD | fifa_shooting x 0.5 | fifa_pace x 0.25 + fifa_dribbling x 0.25 | 0.28 |

**Vazgeçilmezlik Skoru:**
```
importance = (player_rating - team_avg_at_position) / team_std_at_position
  Yuksek z-score = vazgecilmez oyuncu
```

### 3. XGBoost Feature Set (MVP)

| # | Feature | Kaynak |
|---|---------|--------|
| 1 | home_team_strength | Hesaplanmış |
| 2 | away_team_strength | Hesaplanmış |
| 3 | home_form_last5 | Hesaplanmış (SOS normalize) |
| 4 | away_form_last5 | Hesaplanmış (SOS normalize) |
| 5 | home_attack_rating | FIFA + istatistik |
| 6 | home_defense_rating | FIFA + istatistik |
| 7 | away_attack_rating | FIFA + istatistik |
| 8 | away_defense_rating | FIFA + istatistik |
| 9 | home_goals_scored_avg | İstatistik |
| 10 | home_goals_conceded_avg | İstatistik |
| 11 | away_goals_scored_avg | İstatistik |
| 12 | away_goals_conceded_avg | İstatistik |
| 13 | h2h_home_winrate | Tarihsel |
| 14 | h2h_goals_avg | Tarihsel |
| 15 | home_advantage_factor | Lig bazlı ev sahibi avantajı |
| 16 | referee_strictness | Hakem verisi |
| 17 | home_squad_value | FIFA + piyasa |
| 18 | away_squad_value | FIFA + piyasa |
| 19 | form_momentum_diff | Son 5 maç trend farkı |
| 20 | league_position_diff | Sıralama farkı |

### 4. Güven Skoru (Confidence Score)

```python
confidence = 100

# Veri kalitesi kontrolleri
if eksik_oyuncu_verisi:     confidence -= 15
if son_5_mac_eksik:         confidence -= 20
if h2h_data < 3_mac:        confidence -= 10
if odds_data_missing:        confidence -= 10
if model_probability < 0.40: confidence -= 15  # Belirsiz mac

# Bonus
if h2h_data >= 10_mac:      confidence += 5
if tum_veriler_tam:          confidence += 5
```

### 5. Value Bet Tespiti

```
model_odds = 1 / model_probability
value_margin = (bookmaker_odds - model_odds) / model_odds x 100

if value_margin > 5% AND confidence_score > 75:
    "DEGERLI BANKO"
elif value_margin > 3% AND confidence_score > 60:
    "DEGERLI"
else:
    "DEGERSIZ"
```

---

## 🗺️ Uygulama Fazları

### Faz 1: Veri Katmanı (Data Layer) — Hafta 1-2

| # | Görev | Modül | Öncelik |
|---|-------|-------|---------|
| 1.1 | Football-Data.co.uk CSV otomatik indirme scripti | `scripts/download_data.py` | Kritik |
| 1.2 | CSV parser — tüm ligler için standart okuma | `ingestion/csv_loader.py` | Kritik |
| 1.3 | Kaggle FIFA dataset loader | `ingestion/fifa_loader.py` | Kritik |
| 1.4 | SQLite + Supabase dual backend (abstract interface) | `db/` | Kritik |
| 1.5 | Database migration — tüm tablolar | `db/migrations.py` | Kritik |
| 1.6 | Fuzzy matching — takım/oyuncu isim eşleştirme | `ingestion/fuzzy_matcher.py` | Yüksek |
| 1.7 | Veri temizleme pipeline | `preprocessing/cleaner.py` | Yüksek |
| 1.8 | Farklı kaynakları ortak şemaya map | `preprocessing/schema_mapper.py` | Yüksek |
| 1.9 | Hakem istatistikleri çıkarma | `preprocessing/` | Normal |

**Ligler (Top 10 + TR):**

| # | Lig | Football-Data Kodu | Ülke |
|---|-----|-------------------|------|
| 1 | Süper Lig | TR1 | TR |
| 2 | Premier League | E0 | EN |
| 3 | Championship | E1 | EN |
| 4 | La Liga | SP1 | ES |
| 5 | Bundesliga | D1 | DE |
| 6 | Serie A | I1 | IT |
| 7 | Ligue 1 | F1 | FR |
| 8 | Eredivisie | N1 | NL |
| 9 | Primeira Liga | P1 | PT |
| 10 | Jupiler Pro League | B1 | BE |
| 11 | Scottish Premiership | SC0 | SC |

### Faz 2: Feature Engineering (The Brain) — Hafta 2-3

| # | Görev | Modül | Öncelik |
|---|-------|-------|---------|
| 2.1 | Takım gücü hesaplama (%40/%60 + Time Decay) | `features/team_strength.py` | Kritik |
| 2.2 | Son 5 maç formu + SOS normalizasyonu | `features/form_calculator.py` | Kritik |
| 2.3 | Mevki bazlı oyuncu etki puanı | `features/player_impact.py` | Kritik |
| 2.4 | Verimlilik motoru (piyasa değeri vs performans) | `features/efficiency_engine.py` | Yüksek |
| 2.5 | Hakem etki faktörü | `features/referee_impact.py` | Yüksek |
| 2.6 | H2H (Head to Head) istatistikleri | `features/form_calculator.py` | Yüksek |
| 2.7 | Ev sahibi avantajı (lig bazlı) | `features/team_strength.py` | Normal |

### Faz 3: Tahmin Motoru — Hafta 3-4

| # | Görev | Modül | Öncelik |
|---|-------|-------|---------|
| 3.1 | XGBoost multiclass training pipeline | `model/trainer.py` | Kritik |
| 3.2 | Hyperparameter tuning (Optuna) | `model/hypertuner.py` | Kritik |
| 3.3 | Tek maç tahmin fonksiyonu | `model/predictor.py` | Kritik |
| 3.4 | Güven skoru hesaplama | `model/confidence_score.py` | Yüksek |
| 3.5 | Value Bet dedektörü | `evaluator/value_hunter.py` | Kritik |
| 3.6 | Banko maç filtresi | `evaluator/bankometer.py` | Yüksek |
| 3.7 | Backtester (geçmiş sezon simülasyon) | `evaluator/backtester.py` | Yüksek |

### Faz 4: Kalibrasyon ve Arayüz — Hafta 4-5

| # | Görev | Modül | Öncelik |
|---|-------|-------|---------|
| 4.1 | Confusion matrix analizi | `evaluator/confusion_analyzer.py` | Kritik |
| 4.2 | Lig/oran aralığı bazlı hata analizi | `evaluator/confusion_analyzer.py` | Yüksek |
| 4.3 | En başarılı %20'lik dilim tespiti | `evaluator/bankometer.py` | Yüksek |
| 4.4 | Streamlit dashboard — ana tahmin paneli | `app/streamlit_app.py` | Kritik |
| 4.5 | Value Bets sayfası | `app/pages/value_bets.py` | Yüksek |
| 4.6 | Model performans metrikleri sayfası | `app/pages/model_stats.py` | Normal |
| 4.7 | README.md yazımı | `README.md` | Normal |

### Faz 5: Canlı Entegrasyonlar (Gelecek) — Onay sonrası

| # | Görev | Bağımlılık |
|---|-------|------------|
| 5.1 | API-Football entegrasyonu (kadro, sakatlık) | API key gerekli |
| 5.2 | OpenWeatherMap hava durumu | API key gerekli |
| 5.3 | Chemistry Matrix (oyuncu çifti uyumu) | Kadro verisi gerekli |
| 5.4 | Weather and Style Matcher çarpanı | Hava + takım tarzı |
| 5.5 | UEFA/fikstür yorgunluk faktörü | Fikstür verisi gerekli |
| 5.6 | Telegram Bot (/tahmin, /banko, /valuebets) | Tüm sistem hazır |
| 5.7 | Canlı odds karşılaştırma | Odds API gerekli |

---

## Tech Stack

| Katman | Teknoloji | Neden? |
|--------|-----------|--------|
| **Dil** | Python 3.11+ | Data science ekosistemi |
| **ML** | XGBoost + scikit-learn | Gradient boosting SOTA, tablüler veri |
| **Tuning** | Optuna | Bayesian hyperparameter optimization |
| **DB (Lokal)** | SQLite | Zero-config, portable |
| **DB (Cloud)** | Supabase (PostgreSQL) | Satış/paylaşım için hazır |
| **Fuzzy Match** | rapidfuzz | C++ backed, hızlı |
| **CSV** | pandas | Standart |
| **UI** | Streamlit | Hızlı prototip, Python-native |
| **Görselleştirme** | Plotly | İnteraktif grafikler |
| **Env** | python-dotenv | API key yönetimi |
| **Test** | pytest | Standart |

---

## Doğrulama Planı (Verification)

### Otomatik Testler
```bash
# Unit tests
pytest tests/ -v --cov=src --cov-report=html

# Backtest — son sezon tüm maçlar
python scripts/run_backtest.py --season 2024-2025 --leagues all

# Model doğruluğu
python -c "from src.evaluator.backtester import full_report; full_report()"
```

### Başarı Kriterleri

| Metrik | Hedef | Kabul Edilebilir |
|--------|-------|------------------|
| Genel Doğruluk | >= %85 | >= %75 |
| Value Bet ROI | > %5 | > %0 (kayıp yok) |
| Banko Maç İsabet | >= %90 | >= %80 |
| Güven Skoru Korelasyonu | r > 0.7 | r > 0.5 |
| Lig Başına Min. Veri | 5 sezon | 3 sezon |

### Manuel Doğrulama
- Bir haftalık canlı tahmin simülasyonu
- Confusion matrix inceleme (lig/oran bazlı)
- Streamlit dashboard UX kontrolü
- SQLite ve Supabase veri tutarlılığı

---

## Riskler ve Mitigasyon

| Risk | Etki | Mitigasyon |
|------|------|------------|
| %85 doğruluğa ulaşamama | Yüksek | Value Bet ROI'ye odaklan (doğruluk değil karlılık) |
| FIFA dataset güncel olmayabilir | Orta | Son yıl verisi yoksa lig ortalaması fallback |
| Fuzzy match hataları | Orta | Manuel override tablosu + similarity threshold |
| Yeni terfi eden takım (veri yok) | Düşük | Lig ortalaması + kadro değeri fallback |
| Transfer dönemi tutarsızlık | Orta | Transfer tarihi tracking + chemistry reset |
| Football-Data format değişikliği | Düşük | Schema mapper ile soyutlama |

---

## Taşınabilirlik (Portability)

Satış/paylaşım senaryosu için:
- **DB Abstract Layer:** `db/base.py` interface'i sayesinde SQLite / Supabase / PostgreSQL geçişi tek satır config
- **`.env` tabanlı konfigürasyon:** API key'ler, DB connection string'leri environment variable
- **Docker-ready:** Faz 5'te Dockerfile eklenir
- **Lisanslama:** İleride license key sistemi eklenebilir yapıda

---

## MVP Tanımı (Minimum Viable Product)

**MVP = Faz 1 + Faz 2 + Faz 3 + Faz 4**

MVP tamamlandığında sistem şunları yapabilecek:
1. 11 ligin son 5 sezon verisini SQLite'a yükle
2. FIFA oyuncu niteliklerini entegre et
3. Her maç için 20 feature üret
4. XGBoost ile H/D/A olasılıkları tahmin et
5. Güven skoru hesapla
6. Geçmiş oranlarla Value Bet tespiti yap
7. Backtest ile model performansını raporla
8. Streamlit dashboard'da sonuçları göster
