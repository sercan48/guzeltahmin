# MIW Veritabanı & Veri Toplama Katmanı — Detaylı Tasarım

> **Version 2.0** — Market Intelligence Warehouse: Database & Acquisition Layer  
> **Bağımlılık:** [MIW_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_ARCHITECTURE.md)  
> **Durum:** Onaylandı — İmplementasyon için hazır

---

## 1. Odds Veri Sağlayıcı Denetimi

### 1.1 Sağlayıcı Detay Kartları

---

#### 🟢 1. Football-Data.co.uk

| Özellik | Detay |
|---------|-------|
| **Maliyet** | **$0/ay** — Tamamen ücretsiz |
| **Kapsam** | 25+ Avrupa ligi. Pinnacle, Bet365, William Hill, Interwetten, Market Avg/Max. 1X2, O/U 2.5, AH, BTTS |
| **Tarihsel Derinlik** | **1993/94'ten itibaren** (odds verisi 2000/01+; Pinnacle kapanış 2012/13+; Bet365 kapanış 2019/20+) |
| **API Erişimi** | REST API yok — CSV dosya indirme. Sezon bazlı `.csv` dosyaları |
| **Güvenilirlik** | 8/10 — 20+ yıllık kesintisiz hizmet. Pinnacle odds'ları Temmuz 2025'ten itibaren güvenilirliğini yitirdi |
| **Entegrasyon Kolaylığı** | 10/10 — Basit CSV parse, mevcut sistemde entegre |
| **MIW Rolü** | **PRIMARY — Tarihsel backfill kaynağı** |

---

#### 🟢 2. The Odds API

| Özellik | Detay |
|---------|-------|
| **Maliyet** | Free: $0 (500 kredi) · $30 (20K) · **$59 (100K) ← Önerilen** · $119 (5M) · $249 (15M) |
| **Kapsam** | 80+ futbol ligi, 40+ bahisçi (Pinnacle, Bet365, Unibet, 1xBet, Betfair). 1X2, OU, Spreads, Totals |
| **Tarihsel Derinlik** | **Haziran 2020'den itibaren** — 5-10 dakika aralıklı snapshot'lar |
| **API Erişimi** | REST API — Temiz JSON, kredi bazlı billing |
| **Güvenilirlik** | 9/10 — Yüksek uptime, iyi dokümantasyon |
| **Entegrasyon Kolaylığı** | 9/10 — Temiz REST API, mevcut sistemde entegre |
| **MIW Rolü** | **PRIMARY — Canlı odds polling kaynağı** |

---

#### 🔴 3. Pinnacle (Doğrudan API)

| Özellik | Detay |
|---------|-------|
| **Maliyet** | Erişim kapalı — Temmuz 2025'ten itibaren kamu API'si kapatıldı |
| **MIW Rolü** | **INDIRECT — The Odds API üzerinden Pinnacle verisi alınır** |

---

#### 🟡 4. Betfair Exchange

| Özellik | Detay |
|---------|-------|
| **Maliyet** | Basic (1-min): Ücretsiz · Advanced (1-sec): Ücretli · Pro (50ms): Ücretli |
| **Kapsam** | Exchange verisi — hacim, likidite, back/lay. Major Avrupa ligleri |
| **Tarihsel Derinlik** | **Mayıs 2015'ten itibaren** — JSON/TAR format |
| **API Erişimi** | REST API + Historical Portal. Betfair hesabı zorunlu, coğrafi kısıtlamalar |
| **Güvenilirlik** | 8/10 — Kurumsal altyapı, karmaşık format |
| **Entegrasyon Kolaylığı** | 4/10 — JSON-TAR format, hesap zorunluluğu, coğrafi kısıtlama |
| **MIW Rolü** | **SECONDARY — Likidite sinyali ve hacim verisi (Tier 2)** |

---

#### 🟡 5. SportMonks

| Özellik | Detay |
|---------|-------|
| **Maliyet** | Plan bazlı + Odds premium add-on (TXODDS). Tahmini **$50-100/ay** |
| **Kapsam** | 2,300+ lig. Premium Odds: 120+ bahisçi, 40+ market |
| **Tarihsel Derinlik** | **2015/16'dan itibaren** |
| **API Erişimi** | REST API v3 — Temiz JSON, 14 gün ücretsiz deneme |
| **Güvenilirlik** | 8/10 — Kurumsal düzey, aktif geliştirme |
| **Entegrasyon Kolaylığı** | 7/10 — Odds add-on ayrı aktivasyon |
| **MIW Rolü** | **TERTIARY — Çoklu bahisçi konsensüs kaynağı (Tier 2)** |

---

#### 🟢 6. API-Football

| Özellik | Detay |
|---------|-------|
| **Maliyet** | Free: $0 (100/gün) · **Pro: $19 (7,500/gün)** · Ultra: $29 · Mega: $39 |
| **Kapsam** | 1,000+ lig. Maç öncesi + canlı odds. Fikstür, skor, istatistik, sakatlanma |
| **Tarihsel Derinlik** | **2010'dan itibaren** fikstür/skor. Odds tarihsel derinliği sınırlı |
| **API Erişimi** | REST API — JSON, mevcut sistemde entegre |
| **Güvenilirlik** | 7/10 — Genel olarak güvenilir, odds zaman zaman gecikmeli |
| **Entegrasyon Kolaylığı** | 8/10 — Temiz API, mevcut entegrasyon |
| **MIW Rolü** | **SUPPLEMENTARY — Fikstür metadata ve sakatlanma kaynağı** |

---

#### 🔴 7. OddsPortal

| Özellik | Detay |
|---------|-------|
| **Maliyet** | $0 — Web sitesi ücretsiz, API yok |
| **Kapsam** | 100+ lig, 50+ bahisçi. Derin tarihsel arşiv (2005/06+) |
| **API Erişimi** | ❌ API YOK — Sadece web scraping. Selenium/Playwright gerekli |
| **Güvenilirlik** | 5/10 — Scraping kırılgan, site yapısı sık değişir |
| **Entegrasyon Kolaylığı** | 2/10 — ToS ihlali riski, anti-bot korumaları |
| **MIW Rolü** | **❌ KULLANILMAYACAK — Yasal risk ve bakım maliyeti** |

---

#### 🟡 8. OddsJam

| Özellik | Detay |
|---------|-------|
| **Maliyet** | **Özel kurumsal fiyatlandırma** — Tahmini $200-500+/ay |
| **Kapsam** | 100+ sportsbook, derin market kapsamı. 1M+ odds/saniye, WebSocket push |
| **API Erişimi** | REST API + WebSocket. Enterprise-grade dokümantasyon |
| **Güvenilirlik** | 9/10 — Enterprise-grade |
| **Entegrasyon Kolaylığı** | 6/10 — Özel satış süreci, yüksek maliyet |
| **MIW Rolü** | **FUTURE UPGRADE — Faz 4+ için değerlendirilecek** |

---

### 1.2 Sağlayıcı Karşılaştırma Matrisi

| Sağlayıcı | Maliyet | Lig | Bahisçi | Tarihsel | API | Güvenilirlik | Entegrasyon | MIW Puanı |
|-----------|---------|-----|---------|---------|-----|-------------|-------------|-----------|
| **Football-Data** | $0 | 25+ | 6+ | 30+ yıl | CSV | 8/10 | 10/10 | **95** |
| **The Odds API** | $59 | 80+ | 40+ | 6 yıl | REST | 9/10 | 9/10 | **92** |
| **API-Football** | $19 | 1000+ | Sınırlı | 15+ yıl | REST | 7/10 | 8/10 | **72** |
| **SportMonks** | ~$75 | 2300+ | 120+ | 10+ yıl | REST | 8/10 | 7/10 | **70** |
| **Betfair** | Değişken | Major | Exchange | 11+ yıl | REST | 8/10 | 4/10 | **58** |
| **OddsJam** | $200+ | Global | 100+ | ? | REST+WS | 9/10 | 6/10 | **55** |
| **Pinnacle** | Kapalı | — | — | — | ❌ | — | 1/10 | **10** |
| **OddsPortal** | $0 | 100+ | 50+ | 20+ yıl | ❌ | 5/10 | 2/10 | **15** |

### 1.3 Önerilen Kaynak Sıralaması

**TIER 1 — ANA KAYNAKLAR (Faz 1, $78/ay):**
1. 🥇 The Odds API ($59) → Canlı polling, çoklu bahisçi, sharp data
2. 🥈 Football-Data.co.uk ($0) → Tarihsel backfill, kapanış odds'ları
3. 🥉 API-Football ($19) → Fikstür metadata, sakatlanma, takvim

**TIER 2 — GENİŞLETME (Faz 3, +$100-150/ay):**
4. 🔶 SportMonks (~$75) → 120+ bahisçi consensus signal
5. 🔶 Betfair Exchange → Hacim/likidite sinyali

**TIER 3 — GELECEK (Faz 4+):**
6. 🔵 OddsJam ($200+) → Enterprise-grade, WebSocket

**❌ KULLANILMAYACAK:** Pinnacle (doğrudan), OddsPortal

---

## 2. Warehouse Şema Tasarımı

### 2.1 Tablo 1: `bookmakers`

```sql
CREATE TABLE bookmakers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    category            TEXT NOT NULL CHECK(category IN ('sharp', 'mid', 'soft', 'exchange')),
    tier                INTEGER NOT NULL DEFAULT 2,
    api_source          TEXT,
    api_key_name        TEXT,
    trust_score         REAL NOT NULL DEFAULT 0.5,
    reaction_speed_sec  REAL DEFAULT NULL,
    opening_accuracy    REAL DEFAULT NULL,
    overround_avg       REAL DEFAULT NULL,
    overround_std       REAL DEFAULT NULL,
    is_active           INTEGER NOT NULL DEFAULT 1,
    match_count         INTEGER NOT NULL DEFAULT 0,
    last_seen_at        TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX idx_bookmakers_name ON bookmakers(name);
CREATE INDEX idx_bookmakers_category ON bookmakers(category);
CREATE INDEX idx_bookmakers_active ON bookmakers(is_active) WHERE is_active = 1;
```

**Partisyon:** Yok — küçük referans tablosu (~20-50 satır)

---

### 2.2 Tablo 2: `odds_snapshots`

```sql
CREATE TABLE odds_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    bookmaker_id        INTEGER NOT NULL,
    market_type         TEXT NOT NULL,
    selection           TEXT NOT NULL,
    raw_odds            REAL NOT NULL,
    clean_prob          REAL NOT NULL,
    overround           REAL NOT NULL,
    snapshot_window     TEXT NOT NULL,
    time_to_kick_sec    INTEGER NOT NULL,
    captured_at         TIMESTAMP NOT NULL,
    source              TEXT NOT NULL DEFAULT 'the_odds_api',
    source_latency_ms   INTEGER DEFAULT 0,
    CONSTRAINT chk_raw_odds CHECK(raw_odds > 1.0),
    CONSTRAINT chk_clean_prob CHECK(clean_prob > 0.0 AND clean_prob < 1.0),
    CONSTRAINT chk_overround CHECK(overround >= 1.0)
);

CREATE INDEX idx_os_match_time ON odds_snapshots(match_id, captured_at DESC);
CREATE INDEX idx_os_match_window ON odds_snapshots(match_id, snapshot_window);
CREATE INDEX idx_os_book_market ON odds_snapshots(bookmaker_id, market_type);
CREATE INDEX idx_os_match_book_sel ON odds_snapshots(match_id, bookmaker_id, selection);
CREATE INDEX idx_os_captured ON odds_snapshots(captured_at DESC);
CREATE INDEX idx_os_timeseries ON odds_snapshots(match_id, bookmaker_id, market_type, selection, captured_at DESC);
```

**Partisyon:** `captured_at` üzerinden aylık (TimescaleDB geçişinde hypertable). 180 gün retention.

---

### 2.3 Tablo 3: `closing_lines`

```sql
CREATE TABLE closing_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    bookmaker_id        INTEGER NOT NULL,
    market_type         TEXT NOT NULL,
    selection           TEXT NOT NULL,
    closing_odds        REAL NOT NULL,
    closing_prob        REAL NOT NULL,
    closing_overround   REAL NOT NULL,
    opening_odds        REAL,
    opening_prob        REAL,
    total_drift         REAL,
    total_prob_drift    REAL,
    opening_captured_at TIMESTAMP,
    closing_captured_at TIMESTAMP NOT NULL,
    UNIQUE(match_id, bookmaker_id, market_type, selection),
    CONSTRAINT chk_cl_odds CHECK(closing_odds > 1.0),
    CONSTRAINT chk_cl_prob CHECK(closing_prob > 0.0 AND closing_prob < 1.0)
);

CREATE INDEX idx_cl_match ON closing_lines(match_id);
CREATE INDEX idx_cl_match_book ON closing_lines(match_id, bookmaker_id);
CREATE INDEX idx_cl_match_sel ON closing_lines(match_id, market_type, selection);
```

**Partisyon:** Yok — düşük hacim (~270 satır/gün)

---

### 2.4 Tablo 4: `market_movements`

```sql
CREATE TABLE market_movements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    market_type         TEXT NOT NULL DEFAULT '1X2',
    selection           TEXT NOT NULL,
    window_start        TEXT NOT NULL,
    window_end          TEXT NOT NULL,
    window_duration_hr  REAL NOT NULL,
    prob_change         REAL NOT NULL,
    velocity            REAL NOT NULL,
    acceleration        REAL DEFAULT 0.0,
    avg_prob_change     REAL,
    std_prob_change     REAL,
    min_prob            REAL,
    max_prob            REAL,
    bookmaker_count     INTEGER DEFAULT 0,
    is_significant      INTEGER DEFAULT 0,
    direction           TEXT CHECK(direction IN ('SHORTENING', 'DRIFTING', 'STABLE')),
    computed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, market_type, selection, window_start, window_end)
);

CREATE INDEX idx_mm_match ON market_movements(match_id);
CREATE INDEX idx_mm_match_sel ON market_movements(match_id, selection);
CREATE INDEX idx_mm_significant ON market_movements(is_significant) WHERE is_significant = 1;
CREATE INDEX idx_mm_window ON market_movements(window_start, window_end);
```

**Partisyon:** Yok — orta hacim (~315 satır/gün)

---

### 2.5 Tablo 5: `steam_moves`

```sql
CREATE TABLE steam_moves (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    trigger_bookmaker_id INTEGER NOT NULL,
    market_type         TEXT NOT NULL DEFAULT '1X2',
    selection           TEXT NOT NULL,
    direction           TEXT NOT NULL CHECK(direction IN ('HOME', 'DRAW', 'AWAY')),
    prob_delta          REAL NOT NULL,
    time_span_sec       INTEGER NOT NULL,
    odds_before         REAL NOT NULL,
    odds_after          REAL NOT NULL,
    prob_before         REAL NOT NULL,
    prob_after          REAL NOT NULL,
    strength            REAL NOT NULL CHECK(strength BETWEEN 0.0 AND 1.0),
    follower_count      INTEGER DEFAULT 0,
    avg_follow_delay_sec REAL DEFAULT NULL,
    consensus_reached   INTEGER DEFAULT 0,
    was_correct         INTEGER DEFAULT NULL,
    profit_if_followed  REAL DEFAULT NULL,
    time_to_kick_sec    INTEGER NOT NULL,
    detected_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_sm_delta CHECK(prob_delta >= 0.02)
);

CREATE INDEX idx_sm_match ON steam_moves(match_id);
CREATE INDEX idx_sm_strength ON steam_moves(strength DESC);
CREATE INDEX idx_sm_detected ON steam_moves(detected_at DESC);
CREATE INDEX idx_sm_correct ON steam_moves(was_correct) WHERE was_correct IS NOT NULL;
CREATE INDEX idx_sm_match_dir ON steam_moves(match_id, direction);
```

**Partisyon:** Yok — düşük hacim (~3 event/gün)

---

### 2.6 Tablo 6: `market_consensus`

```sql
CREATE TABLE market_consensus (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    snapshot_window     TEXT NOT NULL,
    market_type         TEXT NOT NULL DEFAULT '1X2',
    consensus_favorite  TEXT,
    consensus_score     REAL NOT NULL,
    bookmaker_count     INTEGER NOT NULL,
    avg_home_prob       REAL,
    avg_draw_prob       REAL,
    avg_away_prob       REAL,
    std_home_prob       REAL,
    std_draw_prob       REAL,
    std_away_prob       REAL,
    sharp_home_prob     REAL,
    sharp_draw_prob     REAL,
    sharp_away_prob     REAL,
    soft_home_prob      REAL,
    soft_draw_prob      REAL,
    soft_away_prob      REAL,
    sharp_soft_divergence REAL,
    price_dispersion    REAL,
    avg_overround       REAL,
    spread_width        REAL,
    implied_liquidity   REAL,
    computed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, snapshot_window, market_type)
);

CREATE INDEX idx_mc_match ON market_consensus(match_id);
CREATE INDEX idx_mc_match_window ON market_consensus(match_id, snapshot_window);
CREATE INDEX idx_mc_divergence ON market_consensus(sharp_soft_divergence DESC);
```

**Partisyon:** Yok — düşük hacim (~120 satır/gün)

---

### 2.7 Tablo 7: `clv_history`

```sql
CREATE TABLE clv_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    prediction_id       INTEGER,
    league_code         TEXT NOT NULL,
    market_type         TEXT NOT NULL DEFAULT '1X2',
    selection           TEXT NOT NULL,
    model_probability   REAL NOT NULL,
    prediction_odds     REAL NOT NULL,
    closing_odds        REAL NOT NULL,
    opening_odds        REAL,
    clv_pct             REAL NOT NULL,
    clv_class           TEXT NOT NULL,
    clv_vs_opening      REAL,
    clv_sharp           REAL,
    clv_soft            REAL,
    clv_exchange        REAL,
    steam_move_aligned  INTEGER DEFAULT 0,
    rlm_detected        INTEGER DEFAULT 0,
    consensus_at_pred   REAL,
    regime_at_pred      INTEGER,
    actual_result       TEXT,
    was_correct         INTEGER DEFAULT NULL,
    profit_loss         REAL DEFAULT NULL,
    predicted_at        TIMESTAMP,
    settled_at          TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ch_match ON clv_history(match_id);
CREATE INDEX idx_ch_league ON clv_history(league_code);
CREATE INDEX idx_ch_league_class ON clv_history(league_code, clv_class);
CREATE INDEX idx_ch_created ON clv_history(created_at DESC);
CREATE INDEX idx_ch_correct ON clv_history(was_correct) WHERE was_correct IS NOT NULL;
CREATE INDEX idx_ch_league_time ON clv_history(league_code, created_at DESC);
```

**Partisyon:** `created_at` üzerinden çeyreklik (DuckDB/TimescaleDB geçişinde)

---

### 2.8 Tablo 8: `value_edges`

```sql
CREATE TABLE value_edges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    snapshot_window     TEXT NOT NULL,
    market_type         TEXT NOT NULL DEFAULT '1X2',
    selection           TEXT NOT NULL,
    model_probability   REAL NOT NULL,
    market_clean_prob   REAL NOT NULL,
    soft_market_prob    REAL,
    edge                REAL NOT NULL,
    edge_pct            REAL,
    edge_class          TEXT NOT NULL,
    edge_velocity       REAL DEFAULT 0.0,
    prev_window_edge    REAL,
    market_agreement    REAL DEFAULT 1.0,
    overround_risk      REAL DEFAULT 0.0,
    computed_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(match_id, snapshot_window, market_type, selection),
    CONSTRAINT chk_ve_prob CHECK(model_probability > 0.0 AND model_probability < 1.0),
    CONSTRAINT chk_ve_mkt CHECK(market_clean_prob > 0.0 AND market_clean_prob < 1.0)
);

CREATE INDEX idx_ve_match ON value_edges(match_id);
CREATE INDEX idx_ve_match_window ON value_edges(match_id, snapshot_window);
CREATE INDEX idx_ve_edge_class ON value_edges(edge_class) WHERE edge_class != 'NO_VALUE';
CREATE INDEX idx_ve_edge_desc ON value_edges(edge DESC);
CREATE INDEX idx_ve_high_value ON value_edges(edge_class, edge DESC)
    WHERE edge_class IN ('MEDIUM_VALUE', 'HIGH_VALUE');
```

**Partisyon:** Yok — orta hacim (~360 satır/gün)

---

### 2.9 Tablo 9: `matches` (MIW Genişletmeleri)

```sql
ALTER TABLE matches ADD COLUMN miw_tracked        INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN miw_snapshots_count INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN miw_first_snapshot  TIMESTAMP;
ALTER TABLE matches ADD COLUMN miw_last_snapshot   TIMESTAMP;
ALTER TABLE matches ADD COLUMN kickoff_time        TIMESTAMP;
ALTER TABLE matches ADD COLUMN market_regime       INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN final_consensus     TEXT;

CREATE INDEX idx_matches_miw_tracked ON matches(miw_tracked) WHERE miw_tracked = 1;
CREATE INDEX idx_matches_kickoff ON matches(kickoff_time) WHERE kickoff_time IS NOT NULL;
```

---

## 3. Snapshot Toplama Stratejisi

### 3.1 Zaman Penceresi Tablosu

| Pencere | Adı | Aralık | Polling | Kaynak | Öncelik | Amaç |
|---------|-----|--------|---------|--------|---------|------|
| **T-72h** | SCOUT | 72-49h | 1×/pencere | The Odds API | P10 | İlk keşif, açılış odds |
| **T-48h** | EARLY | 48-25h | 1×/pencere | The Odds API | P8 | Erken hareket, OLV |
| **T-24h** | BASE | 24-13h | 2×/pencere (6 saatte bir) | The Odds API | P6 | Baseline, ilk velocity |
| **T-12h** | TRACK | 12-7h | 2×/pencere (2.5 saatte bir) | The Odds API | P5 | Hareket takibi |
| **T-6h** | FOCUS | 6-3.5h | 4×/pencere (37 dk'da bir) | The Odds API | P3 | Steam move arama |
| **T-3h** | ALERT | 3-1.5h | 6×/pencere (15 dk'da bir) | The Odds API | P2 | RLM tespiti |
| **T-1h** | LIVE | 60-5 dk | 12×/pencere (5 dk'da bir) | The Odds API | P1 | Son sinyaller |
| **T-0** | FINAL | 5-0 dk | BURST (max sıklık) | The Odds API | P0 | Kapanış kayıt |

### 3.2 Pencere Bazlı İşlemler

**T-72h (SCOUT):** İlk odds yakalama → odds_snapshots INSERT → market_consensus oluştur → value_edges oluştur

**T-48h → T-12h:** Düzenli snapshot → velocity hesapla → consensus güncelle → edge güncelle

**T-6h → T-1h:** Yüksek frekanslı izleme → steam move kontrol → RLM kontrol → hareket kayıt

**T-0 (FINAL):** BURST snapshot → closing_lines INSERT → CLV hesapla → clv_history INSERT → polling durdur

### 3.3 Akıllı Önceliklendirme Kuralları

1. **HIGH_VALUE maçlar:** model_edge > 0.05 → priority -= 2
2. **STEAM aktif:** steam_moves.count > 0 ve time_to_kick < 6h → priority -= 3
3. **DÜŞÜK değer:** edge_class = 'NO_VALUE' ve window > T24 → interval × 2
4. **BÜTÇE sınırı:** daily_credits > 900 → sadece P0-P2 poll et

---

## 4. Depolama Tahminleri

### 4.1 Günlük/Aylık/Yıllık

| Dönem | Satır | Depolama |
|-------|-------|----------|
| 1 Gün | ~9,190 | ~1.85 MB |
| 1 Ay | ~275,700 | ~55 MB |
| 1 Yıl | ~2,000,000 | ~500 MB |

### 4.2 API Maliyet

| Kaynak | Plan | Maliyet |
|--------|------|---------|
| The Odds API | 100K | **$59/ay** |
| Football-Data | Ücretsiz | **$0** |
| API-Football | Pro | **$19/ay** |
| **TOPLAM Tier 1** | | **$78/ay** |
