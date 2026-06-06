# MIW Faz 4 — Historical Odds Backfill & Collector Implementation

> **Version 4.0** — Market Intelligence Warehouse: Collector & Backfill Systems  
> **Bağımlılıklar:**  
> [MIW_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_ARCHITECTURE.md) (Faz 1)  
> [MIW_DATABASE_LAYER.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_DATABASE_LAYER.md) (Faz 2)  
> [MIW_INGESTION_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_INGESTION_ARCHITECTURE.md) (Faz 3)  
> **Durum:** Onaylandı

---

## Executive Summary

Bu doküman MIW'nin **veri toplama ve tarihsel doldurma sistemlerinin** operasyonel mimarisini tanımlar. 6 provider implementasyon detayı, backfill pipeline, snapshot scheduler, 4 kuyrukluk mesaj mimarisi, provider failover karar ağacı, doğrulama pipeline'ı, warehouse yazma stratejisi ve operasyonel metrikler.

---

## 1. Provider İmplementasyon Hiyerarşisi

### 1.1 Hiyerarşi

```
OddsProviderInterface (ABC)
├── FootballDataProvider   (FileProvider — CSV)
├── ApiFootballProvider    (RestApiProvider)
├── OddsApiProvider        (RestApiProvider — kredi bazlı)
├── BetExplorerProvider    (ScraperProvider)
├── FuturePremiumProvider  (RestApiProvider — placeholder)
└── ReplayProvider         (CacheProvider — DB replay)
```

### 1.2 Provider Detay Kartları

#### FootballDataProvider

| Özellik | Detay |
|---------|-------|
| **Giriş** | league_code, season, base_url, column_map |
| **Çıkış** | ~3,040 OddsRecord/sezon (380 maç × 4 bookmaker × 2 snapshot) |
| **Güvenilirlik** | Base: kapanış 0.95, açılış 0.90, avg 0.85. Sezon modifiyeri uygulanır |
| **Hata** | 404→sessiz skip, CSV parse→satır atla, bağlantı→3 retry (5s,10s,20s) |
| **Yetenek** | ❌ Canlı, ✅ Tarihsel, ✅ Bulk |

#### OddsApiProvider

| Özellik | Detay |
|---------|-------|
| **Giriş** | api_key, sport, regions, markets, bookmakers filtre, credit_budget |
| **Çıkış** | 15-40 OddsRecord/event (en zengin bahisçi çeşitliliği) |
| **Güvenilirlik** | Base: 0.92, canlı 0.88, tarihsel 0.80. Composite ~0.84 |
| **Hata** | 401→DOWN, kredi bitmesi→P0-P1 odaklan, ağ→3 retry (1s,3s,9s) |
| **Kredi Bütçe** | 0-70% normal, 70-90% T72/T48 atla, 90-95% sadece T6+, 95-100% sadece T0 |
| **Yetenek** | ✅ Canlı, ✅ Tarihsel (10x kredi), ✅ Bulk |

#### ApiFootballProvider

| Özellik | Detay |
|---------|-------|
| **Giriş** | api_key, fixture_id, league_id, season, daily_limit |
| **Çıkış** | 8-15 OddsRecord/fixture |
| **Güvenilirlik** | Base: 0.80, composite ~0.68 |
| **Hata** | 429→60s bekle, günlük limit→RATE_LIMITED, fixture eşleme→fuzzy match |
| **Yetenek** | ✅ Canlı, ⚠️ Tarihsel (sınırlı), ✅ Bulk |

#### BetExplorerProvider

| Özellik | Detay |
|---------|-------|
| **Giriş** | league_url, season, headless, proxy, request_delay_sec |
| **Çıkış** | 5-10 maç/dakika (düşük throughput) |
| **Güvenilirlik** | Base: 0.60, composite ~0.65 |
| **Hata** | Anti-bot→proxy rotate, CAPTCHA→otomatik DEGRADED, timeout→15s→2x retry |
| **Yetenek** | ❌ Canlı, ✅ Tarihsel, ⚠️ Bulk (çok yavaş) |

#### FuturePremiumProvider (Placeholder)

| Özellik | Detay |
|---------|-------|
| **Hedef** | SportMonks (~0.78), OddsJam (~0.85), Betfair (~0.72) |
| **Durum** | enabled: false, NotImplementedError → aktif edilene kadar |

#### ReplayProvider

| Özellik | Detay |
|---------|-------|
| **Giriş** | db_path, replay_speed, match_filter, time_range |
| **Çıkış** | Orijinal verinin birebir kopyası, confidence=1.0 |
| **Güvenilirlik** | Her zaman 1.00, latency <10ms |
| **Hata** | DB bağlantı→3 retry, veri yok→boş liste |
| **Yetenek** | ✅ Tarihsel, ✅ Bulk, ⚠️ Simülasyon modu |

---

## 2. Historical Backfill System

### 2.1 Maç Keşfi (Match Discovery)

4 adımlı pipeline:

1. **ENVANTER:** matches tablosundan 6 sezon (~22,800 maç) al
2. **KAPSAM KONTROLÜ:** Her maç için odds_snapshots'ta kontrol → FULL_COVERAGE / PARTIAL / MISSING
3. **KAYNAK EŞLEME:** Maç tarihine göre provider zinciri belirle (≥2020: FD+OddsAPI+BetExp, 2012-2020: FD+BetExp, <2012: FD only)
4. **KUYRUK:** MISSING/PARTIAL maçları backfill_queue'ya gönder (güncel sezonlar öncelikli)

### 2.2 Odds Import Pipeline

8 adım: DISCOVER → RESOLVE provider → FETCH historical → VALIDATE → SCORE confidence → INTERPOLATE missing → PERSIST → RECONCILE

### 2.3 Eksik Odds Yönetimi

```
Provider 1 (FD.uk) → Başarısız → Provider 2 (OddsAPI Hist) → Başarısız
→ Provider 3 (BetExplorer) → Başarısız → Komşu maç var mı?
  → Evet: Liga ortalaması (confidence=0.15)
  → Hayır: miw_tracked=0, MIW features NULL, model MIW-free mode
```

### 2.4 Confidence Scoring Model

4 katmanlı çarpımsal model:

| Katman | Faktör | Aralık |
|--------|--------|--------|
| **Base** | Kaynak tipi (FD kapanış 0.95 → Liga ortalaması 0.15) | 0.15-0.95 |
| **Sezon** | ≥2019/20: ×1.0, 2012-2019: ×0.95, <2012: ×0.80 | 0.80-1.00 |
| **Cross-Val** | 2+ kaynak aynı: ×1.05, tek: ×0.90, çelişen: ×0.85 | 0.85-1.05 |
| **Zaman** | Gerçek: ×1.0, interpolasyon: ×0.30, liga ort: ×0.15 | 0.15-1.00 |

Final = clamp(base × season × cross × time, 0.05, 1.00). < 0.05 → REJECT.

### 2.5 Reconstruction Rules

1. Kapanış her zaman öncelikli (Pinnacle > Bet365 > Market Avg)
2. Açılış = en eski bilinen snapshot
3. İnterpolasyon sadece ara pencereler (T24 ve T0 asla interpolasyon)
4. Canlı > tarihsel > interpolasyon (overwrite sırası)
5. Bir bookmaker = bir kaynak (aynı pencerede)
6. Minimum 2 bookmaker (consensus için)

---

## 3. Snapshot Scheduler

### 3.1 7 Capture Point

| Pencere | Offset | Tolerans | Öncelik | Retry |
|---------|--------|----------|---------|-------|
| T-72h | -72h | ±30dk | P10 | 1× (2h) |
| T-48h | -48h | ±30dk | P8 | 1× (2h) |
| T-24h | -24h | ±15dk | P6 | 2× (1h, 2h) |
| T-12h | -12h | ±15dk | P5 | 2× (30m, 1h) |
| T-6h | -6h | ±10dk | P3 | 3× (15m, 30m, 1h) |
| T-1h | -1h | ±5dk | P1 | 3× (5m, 10m, 15m) |
| T-0 | -5dk | ±2dk | P0 | 5× (1m, 2m, 3m, 5m, 5m) |

**Scheduler tick:** 60 saniye. PENDING → QUEUED → DONE/FAILED/MISSED.

### 3.2 Missed Snapshot Recovery

- Maç başlamadı + pencere ≤ T24 → GEÇ SNAPSHOT (confidence ×0.85)
- Maç başlamadı + pencere > T24 → SKIPPED_LATE
- Maç başladı + T0 kaçırılmış → Son bilinen odds → closing_lines (confidence ×0.70) + CRITICAL alert
- Maç başladı + T0 değil → sigmoid interpolasyon (confidence ×0.30)

---

## 4. Queue Architecture

4 kuyruk + dead letter:

| Kuyruk | İşlev | Format | Max | Consumer |
|--------|-------|--------|-----|----------|
| **ingestion_q** | Scheduler → Fetch | `{match_id, window, providers, priority}` | 1,000 | 3 worker |
| **processing_q** | Fetch → Normalize+Dedup | `{batch_id, records}` | 5,000 | 2 processor |
| **validation_q** | Normalize → Validate | `{batch_id, normalized_records}` | 3,000 | 1 validator |
| **reconciliation_q** | Valid → DB Write+Consensus+Drift | `{batch_id, valid_records}` | 2,000 | 1 reconciler |
| **dead_letter_q** | 3x nack → Manuel | `{original, error, retry_count}` | ∞ | Manuel |

Faz 1: asyncio.Queue → Gelecek: Redis Streams / Redpanda.

---

## 5. Provider Failover Logic

Tam karar ağacı:

```
Snapshot isteği
  → Provider 1 (Primary): CB CLOSED? → FETCH → Başarılı? → RETURN
                                                Başarısız? → retry < 3?
                                                  → Evet: RETRY (exp backoff)
                                                  → Hayır: CB OPEN → Provider 2

  → Provider 2 (Secondary): CB CLOSED? → FETCH → Başarılı? → RETURN
                                                  Başarısız? → Provider 3

  → Provider 3 (Tertiary): → Başarılı? → RETURN
                             Başarısız? → REPLAY CACHE

  → Replay Cache: Veri var? → RETURN (stale flag)
                   Veri yok? → INTEGRITY_ALERT + MISSED
```

Circuit Breaker: CLOSED → (5 ardışık fail) → OPEN → (30s) → HALF-OPEN → (1 başarılı) → CLOSED

---

## 6. Data Validation Pipeline

4 aşama, 11 kural:

| Aşama | Kurallar | Eylem |
|-------|---------|-------|
| **Duplicate** | Exact hash, DB hash, temporal (<120s) | DROP / SKIP |
| **Impossible** | odds≤1.0, odds>200, prob toplamı, overround | REJECT / FLAG |
| **Stale** | >2h eski, 4 snapshot değişmedi, maç başlamış | FLAG (conf ×0.5) / REJECT |
| **Bookmaker** | 3σ dışı, sharp-soft >8% fark, provider conflict | FLAG / consensus'dan çıkar |

VALID → reconciliation_q. REJECTED → rejected_odds_log. FLAGGED → ileriye taşınır + meta.

---

## 7. Warehouse Population Strategy

3 yazma yolu:

| Yol | Tetikleyici | Strateji |
|-----|-------------|----------|
| **WRITE** | Yeni snapshot/maç/pencere | INSERT OR IGNORE, batch 100/tx |
| **UPDATE** | Canlı veri → interpolasyon overwrite, daha yüksek confidence | UPDATE WHERE confidence artıyorsa |
| **RECONCILIATION** | Günlük cron 02:00 UTC | miw_count doğrula, eksik kapanış yaz, orphan temizle, integrity rapor |

---

## 8. Operational Metrics

| Metrik | Alarm (WARNING) | Alarm (CRITICAL) |
|--------|-----------------|-------------------|
| **provider_uptime_pct** | <90% | <70% |
| **snapshot_success_rate** | <95% | <85% |
| **missing_snapshot_rate** | T1 >5% | T0 >0 |
| **p95_latency_ms** | >5000 | >10000 |
| **warehouse_coverage_pct** | <80% | <60% |
| **credit_usage_pct** | >90% | >95% |

Metrik depolama: miw_metrics tablosu, 90 gün ham, ∞ saatlik aggregat. Alarm: Telegram bot.

---

## 9. Diyagramlar

### Component Diagram (5 Katman)

Scheduler Layer → Queue Layer → Provider Layer (PAL) → Processing Layer → Storage Layer + Monitoring Layer

### Deployment

- Worker process genişletilir (mevcut daily pipeline + yeni MIW görevleri)
- Ayrı SQLite: `guzel_tahmin_miw.db` (sıfır write contention)
- Config: `miw_providers.yaml` + `miw_settings.py` + `.env`

### Implementation Roadmap (11 Faz, 15 Hafta)

| Faz | Hafta | İçerik |
|-----|-------|--------|
| R1 | 1-2 | PAL Core (Interface, Registry, YAML) |
| R2 | 3-4 | Core Providers (FD, OddsAPI, ApiFb, Replay) |
| R3 | 5 | Queue System (4 kuyruk + dead letter) |
| R4 | 6-7 | Snapshot Engine (normalize, dedup, write) |
| R5 | 7-8 | Scheduler (7 pencere, retry, recovery) |
| R6 | 8-9 | Validation Pipeline (4 aşama, 11 kural) |
| R7 | 9-11 | Backfill Engine (discovery, import, interpolation) |
| R8 | 11-12 | Failover (circuit breaker, orchestrator) |
| R9 | 12-13 | Reconciliation (günlük cron, integrity) |
| R10 | 13-14 | Monitoring (metrics, alerts, Telegram) |
| R11 | 15 | BetExplorer (opsiyonel Tier 2 scraper) |

Kritik yol: R1 → R2 → R4 → R5 → R7 + R8 → R9 → R10
