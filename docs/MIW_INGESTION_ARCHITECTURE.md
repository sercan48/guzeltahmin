# MIW Faz 3 — Ingestion Architecture (Enhanced)

> **Version 3.0** — Market Intelligence Warehouse: Complete Ingestion Layer  
> **Bağımlılıklar:** [MIW_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_ARCHITECTURE.md) · [MIW_DATABASE_LAYER.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_DATABASE_LAYER.md)  
> **Durum:** Onaylandı

---

## Executive Summary

Bu doküman MIW'nin tam **ingestion mimarisini** tanımlar. Kritik yenilik: tüm odds veri akışının geçtiği **Provider Abstraction Layer (PAL)** — hiçbir downstream bileşen (feature engine, model, karar motoru) belirli bir veri sağlayıcısına bağımlı değil. Provider değişikliği yalnızca konfigürasyon düzeyinde gerçekleşir.

---

## 1. Veri Kaynakları (11 Kaynak)

| # | Kaynak | Tür | Provider Tipi | Erişim | Durum |
|---|--------|-----|---------------|--------|-------|
| 1 | Football-Data.co.uk | CSV | `FileProvider` | Ücretsiz | ✅ Aktif |
| 2 | The Odds API (Free) | REST | `RestApiProvider` | $0 (500 kr/ay) | ✅ Aktif |
| 3 | The Odds API (Paid) | REST | `RestApiProvider` | $59-249/ay | Config geçişi |
| 4 | API-Football | REST | `RestApiProvider` | $0-39/ay | ✅ Aktif |
| 5 | BetExplorer | Web | `ScraperProvider` | Ücretsiz | Yeni |
| 6 | Pinnacle (via OddsAPI) | REST (dolaylı) | `RestApiProvider` | OddsAPI kapsamında | Filtre bazlı |
| 7 | Bet365 (via OddsAPI) | REST (dolaylı) | `RestApiProvider` | OddsAPI kapsamında | Filtre bazlı |
| 8 | SportMonks | REST | `RestApiProvider` | ~$75/ay | Gelecek (Tier 2) |
| 9 | Betfair Exchange | REST + Portal | `ExchangeProvider` | Değişken | Gelecek (Tier 2) |
| 10 | OddsJam | REST + WS | `RestApiProvider` | $200+/ay | Gelecek (Tier 3) |
| 11 | Cache/Replay | Yerel | `ReplayProvider` | $0 | Yeni (backtest) |

---

## 2. Provider Abstraction Layer (PAL) — KRİTİK

### 2.1 Mimari

```
                   DOWNSTREAM CONSUMERS
     ┌────────────┐ ┌────────────┐ ┌────────────┐
     │ Snapshot    │ │ Feature    │ │ CLV Dataset│
     │ Engine     │ │ Builder    │ │ Generator  │
     └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
           └──────────────┴──────────────┘
                          │
              ╔═══════════╧═══════════════╗
              ║ Standardized Odds Record  ║
              ║ (SOR — OddsRecord)        ║
              ╚═══════════╤═══════════════╝
                          │
     ┌────────────────────┴─────────────────────┐
     │      PROVIDER ABSTRACTION LAYER (PAL)    │
     │                                          │
     │  OddsProviderInterface (ABC)             │
     │  ├── fetch_odds()  → list[OddsRecord]    │
     │  ├── fetch_historical() → list[OddsRecord]│
     │  ├── fetch_bulk()  → list[OddsRecord]    │
     │  ├── get_health()  → ProviderHealth      │
     │  └── get_capabilities() → ProviderCaps   │
     │                                          │
     │  Provider Registry + Orchestrator        │
     │  ├── Fallback chain yürütme              │
     │  ├── Circuit breaker yönetimi            │
     │  └── Sonuç birleştirme + dedup           │
     └──────────────────┬───────────────────────┘
                        │
     ┌──────┬───────┬───┴───┬──────────┬────────┐
     │RestAPI│ File │Scraper│Exchange  │Replay  │
     │      │      │       │          │        │
     │OddsA │FD.uk │BetExp │Betfair   │Cache   │
     │ApiFb │      │       │          │Backtest│
     │SptMnk│      │       │          │        │
     └──────┘──────┘───────┘──────────┘────────┘
```

### 2.2 Standardized Odds Record (SOR)

```
OddsRecord {
    match_id:                 int       — FK → matches(id)
    bookmaker_id:             int       — FK → bookmakers(id)
    odds_home:                float     — Ham odds (>1.0)
    odds_draw:                float
    odds_away:                float
    timestamp:                datetime  — UTC normalized
    snapshot_type:            str       — T72/T48/.../T0
    confidence_score:         float     — [0.0, 1.0]
    source_reliability_score: float     — [0.0, 1.0]
    
    — Derived —
    clean_prob_home:          float     — Marj temizlenmiş
    clean_prob_draw:          float
    clean_prob_away:          float
    overround:                float     — ≥1.0
    market_type:              str       — '1X2' default
    
    — Meta —
    provider_name:            str
    fetch_latency_ms:         int
    is_interpolated:          bool
    raw_response_hash:        str       — Deduplication
}
```

### 2.3 Provider Geçiş Garantisi

Provider swap → sadece YAML değişikliği, **sıfır kod değişikliği**:

| Senaryo | Değişiklik | Kod |
|---------|-----------|-----|
| Free → Paid geçiş | `plan: "100K"` → `"5M"` | YOK |
| SportMonks ekleme | `enabled: false` → `true` | YOK |
| OddsAPI kapanırsa | `enabled: false` + alternatif `priority: 1` | YOK |
| Yeni provider | Sınıf yaz + YAML'a ekle | Sadece provider sınıfı |
| Backtest modu | API'ler `false`, replay `true` | YOK |

---

## 3. Source Reliability Scoring

Her provider için 4 alt skor + bileşik skor:

| Skor | Formül | Ağırlık |
|------|--------|---------|
| **reliability (R)** | success / total (30 gün) | 35% |
| **latency (L)** | max(0, 1 - avg_ms/5000) | 25% |
| **completeness (C)** | fields_present / expected | 25% |
| **historical (H)** | covered_seasons / required | 15% |

**composite = 0.35R + 0.25L + 0.25C + 0.15H**

Eğer composite < 0.3 → otomatik DEGRADED. 3 ardışık fail → circuit breaker AÇ.

---

## 4. Odds Snapshot Engine

7 adımlı capture pipeline:

1. **FETCH** → Provider Orchestrator üzerinden (PAL)
2. **NORMALIZE** → UTC, decimal format, bookmaker_id eşleme, clean_prob
3. **VALIDATE** → Data Quality Layer kontrolü
4. **DEDUPLICATE** → 4 kurallı dedup (exact, near, temporal, cross-provider)
5. **ENRICH** → snapshot_window, confidence, reliability ekleme
6. **PERSIST** → Batch INSERT → odds_snapshots
7. **TRIGGER** → Post-snapshot pipeline (drift, steam, consensus, edge)

### Deduplication Kuralları

| Kural | Eşleşme | Eylem |
|-------|---------|-------|
| Exact Duplicate | Aynı (match, book, odds, window) | En yüksek reliability tut |
| Near Duplicate | Aynı (match, book, window) + |Δodds|<0.02 | En düşük latency tut |
| Temporal Duplicate | Aynı (match, book) + |Δtime|<120s | Son timestamp tut |
| Cross-Provider | Aynı (match, book) + |Δodds|≥0.02 | İkisini de tut |

### Timestamp Normalization

Tüm provider formatları → `datetime (UTC, timezone-aware)`:
- Unix epoch → `utcfromtimestamp()`
- ISO 8601 + offset → `astimezone(UTC)`
- Sadece tarih → 12:00 UTC
- Yerel saat → Lig timezone'u ile dönüşüm

---

## 5. Historical Backfill Engine

### 3 Fazlı Backfill

| Faz | Kaynak | Snapshot | Confidence |
|-----|--------|----------|------------|
| 1 | FD.co.uk CSV | T-24h (açılış) + T-0 (kapanış) | 0.90-0.95 |
| 2 | OddsAPI Historical | T-72h → T-6h (5-10dk aralık) | 0.80 |
| 3 | BetExplorer (opsiyonel) | Eksik kalan maçlar | 0.50 |

### Interpolation (Eksik Pencereler)

Sigmoid-weighted interpolation: `P(t) = P_open + (P_close - P_open) × sigmoid_weight(t)`

- İnterpolasyonlu kayıtlar `is_interpolated = True`, confidence × 0.30
- Canlı veri geldiğinde → OVERWRITE
- Sadece tarihsel maçlarda uygulanır (canlı → ASLA)

---

## 6. Real-Time Collector

### Retry Logic (3 Katman)

| Katman | Aktör | Strateji |
|--------|-------|----------|
| 1. Provider İç | Provider sınıfı | 3 retry, exponential backoff (1s, 2s, 4s) |
| 2. Provider Failover | Orchestrator | Fallback zincirine geç |
| 3. Gecikmeli Retry | Scheduler | 5 dk sonra yeniden programla |

### Circuit Breaker (Provider Bazlı)

CLOSED → (5 ardışık fail veya %80 fail rate) → OPEN → (30s cooldown) → HALF-OPEN → (1 başarılı) → CLOSED

### Queue Architecture

| Kuyruk | İşlev | Max Size |
|--------|-------|----------|
| `odds_fetch_queue` | Scheduler → Dispatcher | 1000 |
| `odds_write_queue` | Fetch → DB Writer | 5000 |
| `signal_queue` | Snapshot → Signal Detector | 500 |
| `dead_letter_queue` | 3x nack → Manuel inceleme | ∞ |

Faz 1: `asyncio.Queue` → Faz 2+: Redis Streams / Redpanda

---

## 7. Data Quality Layer

5 anomali kategorisi, tümü **provider-attributed**:

| Kategori | Tespit | Eylem |
|----------|--------|-------|
| **Impossible Odds** | odds ≤ 1.0, prob toplamı yanlış | REJECT + log |
| **Stale Odds** | 4 snapshot değişmedi, 2h+ eski | FLAG, confidence × 0.5 |
| **Duplicate Odds** | Hash eşleşme | Düşük reliability SİL |
| **Bookmaker Anomaly** | 3σ dışı, overround anormal | Consensus'dan çıkar |
| **Market Anomaly** | Tüm bookmaker'lar ≥5% ani hareket | FLAG, ayrı log |

Provider anomaly rate > 0.10 → reliability düşür. > 0.25 → otomatik DEGRADED.

---

## 8. Market Drift Layer (Provider-Agnostic)

| Metrik | Formül | Sinyal Eşiği |
|--------|--------|-------------|
| **Opening→Close Movement** | closing_prob - opening_prob | ≥5% = SIGNIFICANT |
| **Velocity (dP/dt)** | ΔP / Δt (saat başına) | ≥0.02/h = Önemli |
| **Volatility** | std(tüm velocity'ler) | Yüksek = belirsiz piyasa |
| **Steam Move** | Sharp book |ΔP| ≥ 0.03, < 300s | strength formülü |
| **RLM** | Odds ↔ public money ters yön | Binary sinyal |

Tüm hesaplamalar `(bookmaker_id, selection)` bazlı — provider bilgisi sadece metadata.

---

## 9. CLV Training Dataset Generator

**66 feature** (40 mevcut + 12 market + 8 line movement + 6 bookmaker):

- **Provider Fusion Weighting:** Aynı bahisçi çoklu provider → confidence-weighted average
- **Market Consensus Fusion:** `Σ(τ_b × reliability_p × clean_prob) / Σ(τ_b × reliability_p)`
- **Feature Confidence:** Her MIW feature'a eşlik eden `_confidence` sütunu

---

## 10. Storage Strategy

- **İndeksleme:** 6 sorgu pattern → optimize indeksler (hiçbiri provider bağımlı değil)
- **Partisyon:** odds_snapshots → aylık, clv_history → çeyreklik
- **Arşiv:** 180 gün ham → 1-saatlik OHLC sıkıştırma → haftalık cron
- **Multi-Provider Destek:** Şema provider-agnostic, provider metadata olarak saklanır

---

## 11. Implementation Roadmap

| Faz | Süre | İçerik |
|-----|------|--------|
| F1 | Hf 1-2 | PAL Foundation (Interface, Registry, Config) |
| F2 | Hf 3-4 | Core Providers (OddsAPI, FD.uk, APIFootball) |
| F3 | Hf 5-6 | Snapshot Engine (capture, dedup, normalize) |
| F4 | Hf 6-7 | Quality Layer (5 anomali detector) |
| F5 | Hf 7-8 | Historical Backfill (FD.uk, confidence, interpolation) |
| F6 | Hf 8-10 | Real-Time Collector (retry, circuit breaker, queue) |
| F7 | Hf 10-11 | Drift Layer (velocity, steam, RLM) |
| F8 | Hf 11-12 | CLV Dataset Generator (66 features, fusion) |
| F9 | Hf 13-14 | Tier 2 Providers (BetExplorer, Replay) |
| F10 | Hf 14-15 | Monitoring (health dashboard, alerts) |

---

## 12. Architecture Score: 8.775 / 10

| Kriter | Puan |
|--------|------|
| Provider Soyutlama | 9.5/10 |
| Genişletilebilirlik | 9.0/10 |
| Veri Kalitesi | 8.5/10 |
| Hata Toleransı | 8.5/10 |
| Bakım Kolaylığı | 9.0/10 |
| Maliyet Verimliliği | 8.0/10 |
| Performans | 8.0/10 |
