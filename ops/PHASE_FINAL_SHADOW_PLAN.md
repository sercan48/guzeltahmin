# PHASE-LIVE FINAL — Reality Validation Mode (SHADOW Doğrulama Planı)

> **Mod:** Operasyonel doğrulama. **Kod donduruldu.** Yeni prediction / ML /
> signal generation / monetization özelliği EKLENMEZ. Sadece mevcut
> `ProductionHarness` + `ValidationHarness` + `monetization` katmanı gerçek
> provider'lara bağlanır ve gözlemlenir.

---

## 0. İlke ve Kapsam

| Kural | Durum |
|---|---|
| Yeni özellik | YASAK |
| Yeni prediction logic | YASAK |
| Yeni ML | YASAK |
| Yeni signal generation | YASAK |
| Yeni monetization özelliği | YASAK |
| Telegram publish | **dry-run (kapalı)** — tüm SHADOW boyunca |
| Betting execution | YOK (flat-unit paper ROI) |

SHADOW'un amacı **karar değil ölçüm**: operasyonel eksikler, gerçek-veri
uyumsuzlukları, provider mapping hataları, latency/completeness sorunları,
replay/settlement sapmaları tespit edilir.

---

## 1. Kullanılan Mevcut Bileşenler (değişmeden)

| Katman | Bileşen | Rol |
|---|---|---|
| L3 | `ServiceRuntime` / `build_runtime` | Sürekli döngü, scheduler→bridge→gate→settlement |
| L4 | `ValidationHarness`, `FeedMonitor`, `ReplayVerifier`, `SettlementVerifier`, `ReadinessScore` | Completeness, latency, replay, settlement doğrulama |
| L6 | `ProductionProfile`, `ProviderValidator`, `OperationalMetrics`, `AlertEngine`, `ProductionHarness` | Gerçek-feed dry-run, pre-flight, uptime/availability, alarmlar |
| L7 | `PublicChannelProfile`, `PublicDeliveryMetrics`, `DashboardExporter` | Public teaser metrikleri (yalnız gözlem) |
| M8 | `SettlementLedger` (`realized_roi`, `realized_clv`, `total_roi`, `mean_roi`) | ROI / CLV / hit-rate kaynağı |
| M-CLV | `CLVFoundation` | Closing line value ölçümü |
| M9 | `ControlPlane` (`replay`, `verify_chain`, `force_kill`, HALT) | Control state + kill-switch kaynağı |
| L5 | `RevenueSimulator` (`funnel_snapshot`, `mrr_projection`, `churn_indicators`) | Monetization funnel kaynağı |

---

## 2. Günlük Metrik Kaynak Haritası (KRİTİK OPERASYONEL BULGU)

İstenen 10 günlük metriğin tamamı mevcut bileşenlerden **okuma yoluyla**
elde edilebilir. Hiçbiri için yeni koda gerek yoktur. Ancak bir kısmı
`ProductionDailyReport`'un *otomatik* alanlarında değil — bunlar **günlük ops
derleme prosedürüyle** (mevcut read-only metodlar çağrılarak) toplanır.

| # | Günlük metrik | Üreten mevcut bileşen | Ledger | Otomatik raporda? |
|---|---|---|---|---|
| 1 | Sinyal sayısı | `OperationalMetrics.signals_total` | control.db.gate | ✅ Evet |
| 2 | Settlement sayısı | `SettlementVerifier` / `OperationalMetrics.settlements_total` | settlement ledger | ✅ Evet |
| 3 | **ROI** | `SettlementLedger.total_roi / mean_roi` | settlement.db | ⚠️ Türetme (read-only sorgu) |
| 4 | **CLV** | `CLVFoundation.compute` / `SettlementLedger.realized_clv` | settlement.db + truth.db | ⚠️ Türetme |
| 5 | **Hit rate** | settlement ledger `WON/(WON+LOST)` | settlement.db | ⚠️ Türetme |
| 6 | Feed completeness | `FeedMonitor.completeness` / `OperationalMetrics.snapshot_completeness` | in-memory | ✅ Evet |
| 7 | Provider latency | `LatencyTracker` p50/p95/p99 | in-memory | ✅ Evet (monitoring) |
| 8 | Control state transitions | `ControlPlane` audit + `gateway.monitor()` | control.db | ✅ Evet |
| 9 | Kill-switch olayları | `ControlPlane` HALT / `AlertEngine` | control.db | ⚠️ Alarm var, sayım türetilir |
| 10 | Monetization funnel | `RevenueSimulator.funnel_snapshot()` | monetization DBs | ⚠️ Ayrı çağrı |

> **Operasyonel boşluk #1 (raporlama):** 3,4,5,9,10 numaralı metrikler tek
> günlük JSON'da otomatik birleşmiyor. Çözüm **yeni kod değil**, günlük bir
> "rapor derleme adımı": var olan read-only metodları (`SettlementLedger`,
> `CLVFoundation`, `RevenueSimulator`) çağırıp `prod-<gün>.json` yanına
> `metrics-<gün>.json` olarak yazan operatör prosedürü. SHADOW Hafta-1'in ilk
> görevi budur.

---

## A) 30 GÜNLÜK SHADOW PLANI

### Topoloji

```
[Gerçek Pinnacle]  [Gerçek Betfair]  [Betfair Outcome]
        │                │                │
        └──── UrllibHttpClient (EnvSecretProvider) ────┐
                                                        ▼
              ProductionHarness (dry_run=True, publish KAPALI)
                ├─ ServiceRuntime.run_once()  (poll=30s)
                ├─ FeedMonitor + LatencyTracker
                ├─ OperationalMetrics + AlertEngine
                ├─ ReplayVerifier (her 100 iterasyon)
                └─ ProductionDailyReport  → prod-<gün>.json
                          +
              Günlük Ops Derleme (read-only):
                SettlementLedger → ROI/CLV/hit-rate
                RevenueSimulator → funnel
                          → metrics-<gün>.json
```

### Ön Koşul (Gün 0 — başlamadan)

1. `ProviderValidator.run(required_secrets, endpoints)` → tüm kimlik bilgileri
   PASS olmalı (`PINNACLE_API_KEY`, `BETFAIR_APP_KEY`, `BETFAIR_SESSION_TOKEN`).
2. Reachability probe ile 3 endpoint PASS.
3. `ProductionProfile.validate()` ve `dry_run=True` doğrulandı.
4. Boş ledger'larla başlangıç; `verify_chain()` = True.
5. Betfair session-token yenileme prosedürü (8 saat) operatörde hazır.

### Faz Takvimi

| Faz | Günler | Odak | Çıkış kapısı |
|---|---|---|---|
| **F1 — Bağlantı & Mapping** | 1–5 | Provider mapping, FixtureMap, kimlik/erişim, ilk snapshot'lar | Mapping hata oranı < %1, pre-flight stabil |
| **F2 — Completeness & Latency** | 6–12 | Feed completeness, provider latency dağılımı, degraded mod davranışı | Completeness ≥ %90, p95 latency ≤ 2s |
| **F3 — Settlement & Replay** | 13–20 | Gerçek maç settlement, ROI/CLV/hit-rate akışı, replay determinizmi | Settlement sapması = 0, restart-replay özdeş |
| **F4 — Kararlılık & Funnel** | 21–30 | Uptime, kill-switch davranışı, monetization funnel gözlemi | 7 gün kesintisiz, alarm gürültüsü düşük |

### Günlük Operasyon Rutini (her gün, sabit saatte)

1. `ProductionHarness` çalışır durumda mı? `OperationalMetrics.snapshot()` →
   uptime, availability kontrolü.
2. `prod-<gün>.json` üretildi mi? `readiness.overall` ve `alerts.total` oku.
3. Ops derleme: `SettlementLedger` ROI/CLV/hit-rate + `RevenueSimulator` funnel
   → `metrics-<gün>.json`.
4. `ReplayVerifier.check()` → `chain_valid=True` ve replay sayıları monoton mu?
5. Alarm kovası: degraded / outage / completeness / replay-integrity adetleri.
6. Provider mapping reddi (unmapped fixture) sayısı.
7. Bulgu varsa **Risk Register**'a kaydet (Bölüm E).

### Haftalık İnceleme

- Haftalık trend: completeness, latency p95, hit-rate, ROI, kill-switch adedi.
- `DashboardExporter` trend serileri (readiness / provider-health / signal-volume).
- Açık operasyonel bulguların kapanış durumu.

### SHADOW'da Aranan 5 Sınıf Bulgu

| Sınıf | Nasıl tespit edilir | Sinyal |
|---|---|---|
| Operasyonel eksik | Günlük rapor alanı boş/None | Metrik üretilmiyor |
| Gerçek-veri uyumsuzluğu | Parser None / beklenmeyen alan | Provider şeması değişmiş |
| Provider mapping problemi | FixtureMap unmapped sayacı | match_id ↔ fixture_id kopuk |
| Latency / completeness | p95 > eşik, completeness < eşik | Feed kalitesi düşük |
| Replay / settlement sapması | `verify_chain=False` veya restart farkı | Determinizm bozulmuş |

---

## B) PAPER GEÇİŞ KRİTERLERİ (SHADOW → PAPER)

PAPER = aynı dry-run, fakat sinyaller "yayınlanmış sayılarak" kayda alınır
(hâlâ gerçek Telegram YOK). Geçiş için **30 günün tamamı** + aşağıdakiler:

| Kriter | Eşik |
|---|---|
| Kesintisiz uptime | Son 7 gün ≥ %99 (availability) |
| Feed completeness | 30 gün ortalama ≥ %95 |
| Provider latency p95 | ≤ 2000 ms (her iki sağlayıcı) |
| Settlement sapması | 0 (tetiklenen = kaydedilen) |
| Replay determinizmi | Tüm restart'larda özdeş; `chain_valid=True` %100 |
| Provider mapping hata oranı | < %0.5 |
| Kill-switch davranışı | HALT yalnız gerçek risk eşiğinde; yanlış-pozitif yok |
| Günlük rapor bütünlüğü | 10 metriğin 10'u her gün dolu (boşluk #1 kapandı) |
| ROI ölçüm tutarlılığı | `total_roi`/`mean_roi` settlement adediyle tutarlı |
| Açık kritik bulgu | Risk Register'da CRITICAL açık kayıt = 0 |

**Çıkış kapısı sahibi:** Operatör + `ReadinessScore.verdict ≥ CONDITIONAL_GO`.

---

## C) MICRO GEÇİŞ KRİTERLERİ (PAPER → MICRO)

MICRO = çok dar kapsamda **gerçek publish** (örn. tek private kanal, küçük
abone kümesi), flat-unit takip devam. Geçiş için PAPER kriterlerine ek:

| Kriter | Eşik |
|---|---|
| PAPER süresi | ≥ 14 gün kesintisiz, kriter ihlali yok |
| Hit-rate ölçümü | İstatistiksel anlam için ≥ N settle (ör. ≥ 100 settle) |
| CLV işareti | Ortalama CLV ≥ 0 (kapanışı dövme eğilimi pozitif/nötr) |
| ROI dağılımı | `mean_roi` makul bant içinde, aşırı varyans yok |
| Suppression doğruluğu | SUPPRESS/HALT sinyali **hiçbir** kanala sızmadı (L7 3-katman guard) |
| Watermark bütünlüğü | `WatermarkInjector` encode/decode %100 audit eşleşmesi |
| Quota & delay determinizmi | `DelayScheduler` replay-eşit, çift-enqueue yok |
| Session-token rotasyonu | 8 saatlik yenileme kesintisiz çalıştı |
| Telegram dry-run→live anahtarı | Tek config flag, geri-alınabilir, test edildi |
| Kill-switch tatbikatı | `force_kill` → tüm publish anında durdu (manuel tatbikat) |

---

## D) İLK ÜCRETLİ TELEGRAM AÇILIŞ KRİTERLERİ (MICRO → PAID)

İlk ücretli kanal (BASIC/PRO) açılışı için MICRO kriterlerine ek:

| Kriter | Eşik / Şart |
|---|---|
| MICRO süresi | ≥ 14 gün, ödeme öncesi tam stabil |
| Tier routing doğruluğu | TIER_S/A→PRO, gecikmeler (PRO 0 / BASIC 15dk / FREE 4s) %100 doğru |
| İçerik sızıntısı | PRO/BASIC FULL içeriği FREE'ye **asla** ulaşmadı (kanıtlı, 30+ gün) |
| Funnel gözlemi | `RevenueSimulator` funnel ≥ 1 tam ay gerçek veriyle dolu |
| Churn göstergeleri | `churn_indicators()` izleniyor, kritik kırmızı yok |
| Para iadesi/şikâyet süreci | Operasyonel SOP yazılı (kod değil, prosedür) |
| Yasal/uyum | Abonelik şartları, sorumluluk reddi, KVKK/GDPR hazır |
| MRR projeksiyon temeli | `mrr_projection()` gerçek abone sayısıyla anlamlı |
| Geri-dönüş planı | Tek komutla dry-run'a dönüş (kill-switch + config) |
| Destek kapasitesi | Token rotasyon + olay müdahale operatörü hazır |

> **Sert kural:** Ücretli açılış, yalnızca D'deki *tüm* satırlar yeşilse.
> Tek bir içerik-sızıntısı kanıtı açılışı bloke eder.

---

## E) GO-LIVE RISK REGISTER

| ID | Risk | Olasılık | Etki | Tespit (mevcut bileşen) | Azaltım (operasyonel) | Tetik |
|---|---|---|---|---|---|---|
| R1 | Betfair session-token süresi dolması (8s) | Yüksek | Orta | `AlertEngine` provider_availability düşüşü | Zamanlı rotasyon SOP; degraded mod otomatik | Availability < %90 |
| R2 | Provider şema değişikliği → parser None | Orta | Yüksek | Completeness düşüşü + boş alan | Parser çıktısını günlük denetle; sağlayıcı değişiklik takibi | Completeness < %90 |
| R3 | FixtureMap mapping kopuğu (unmapped) | Orta | Yüksek | Unmapped sayacı | Mapping tablosu günlük doğrulama | Mapping hata > %0.5 |
| R4 | Latency artışı / rate-limit | Orta | Orta | `LatencyTracker` p95 | Token-bucket ayarı; circuit breaker | p95 > 2s |
| R5 | Settlement sapması (tetik≠kayıt) | Düşük | Kritik | `SettlementVerifier` + ledger karşılaştırma | Günlük mutabakat; durdur+incele | Sapma ≠ 0 |
| R6 | Replay/hash-chain bozulması | Düşük | Kritik | `verify_chain()` / `ReplayVerifier` | Append-only ledger; restart testi | `chain_valid=False` |
| R7 | Yanlış-pozitif kill-switch (HALT) | Orta | Orta | `ControlPlane` HALT transitions | Eşik kalibrasyonu (yalnız gözlem) | Beklenmeyen HALT |
| R8 | İçerik sızıntısı (FULL→FREE) | Düşük | Kritik | L7 3-katman guard + `assert_no_leakage` | Açılış-bloke kuralı; kanıt arşivi | Herhangi 1 sızıntı |
| R9 | Tek-instance kilidi başarısız (çift süreç) | Düşük | Yüksek | `SingleInstanceLock` | PID kilidi; deployment disiplini | İkinci başlatma reddi |
| R10 | Crash sonrası state kaybı | Düşük | Yüksek | Restart-replay testi | SQLite append-only; tekrar-açılış | Restart farkı |
| R11 | Veri kalitesi düşük → yanıltıcı ROI/CLV | Orta | Yüksek | CLV/ROI dağılım denetimi | İstatistiksel anlam eşiği (N≥100) | Aşırı varyans |
| R12 | Monetization funnel boş/yanlış | Düşük | Orta | `RevenueSimulator` denetimi | Günlük funnel snapshot doğrulama | None/0 alanlar |
| R13 | Telegram dry-run→live yanlışlıkla açık | Düşük | Kritik | Config denetimi (`dry_run` flag) | Açılış öncesi config review; varsayılan dry-run | `dry_run=False` beklenmedik |
| R14 | Operatör yorgunluğu / 30 gün izleme | Orta | Orta | — | Günlük rutin checklist; haftalık devir | Kaçırılan günlük rapor |

### Risk Register İşletimi

- Her SHADOW günü yeni bulgu → ID atanır, olasılık/etki puanlanır.
- CRITICAL etki + açık durum → PAPER geçişini **bloke eder**.
- Haftalık incelemede durum güncellenir (Açık / İzlemede / Kapandı).

---

## Özet Karar Akışı

```
SHADOW (30g, publish kapalı)
   └─ B kriterleri ✓ ──► PAPER (yayın "sayılır", Telegram yok)
        └─ C kriterleri ✓ ──► MICRO (dar gerçek publish)
             └─ D kriterleri ✓ ──► PAID (ilk ücretli kanal)

Her aşamada: ReadinessScore + Risk Register + kill-switch tatbikatı.
Tek CRITICAL açık kayıt → ilerleme durur.
```

**Bu faz boyunca tek satır prediction/ML/signal/monetization kodu eklenmez.**
Tüm ilerleme, mevcut bileşenlerin gerçek dünyada gözlemlenmesiyle elde edilir.
