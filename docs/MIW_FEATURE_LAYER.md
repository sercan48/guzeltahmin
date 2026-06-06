# MIW Faz 5 — Market Intelligence Feature Layer & Signal Engine

> **Version 5.0** — Market Intelligence Warehouse: Feature Layer & Signal Engine  
> **Bağımlılıklar:**  
> [MIW_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_ARCHITECTURE.md) (Faz 1)  
> [MIW_DATABASE_LAYER.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_DATABASE_LAYER.md) (Faz 2)  
> [MIW_INGESTION_ARCHITECTURE.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_INGESTION_ARCHITECTURE.md) (Faz 3)  
> [MIW_COLLECTOR_BACKFILL.md](file:///c:/Users/WIN/Desktop/Güzel Tahmin/guzeltahmin/docs/MIW_COLLECTOR_BACKFILL.md) (Faz 4)  
> **Durum:** Onaylandı

---

## Executive Summary

Bu doküman, ham odds snapshot'larını **tahmine katkı sağlayan piyasa sinyallerine** dönüştüren tam Feature Layer'ı tanımlar. 18 temel market feature'ın matematiksel tanımı, kullanım sınıflandırması (prediction-safe / training-only / CLV-only), sinyal bozulma mantığı, piyasa rejim tespiti ve lig-spesifik ayarlamalar.

Mevcut feature katmanı (team_strength, form_calculator, xg_features) tamamen **takım ve performans bazlı**. MIW Feature Layer, bunlara **piyasa ve bahisçi davranışı bazlı 18 yeni sinyal** ekler.

---

## 1. Feature Catalog — 18 Market Feature

### 1.1 Kullanım Sınıflandırması

- 🟢 **PREDICTION-SAFE** — Tahmin zamanında kullanılabilir. Kapanış odds'u KULLANILMAZ
- 🟡 **TRAINING-ONLY** — Sadece model eğitiminde kullanılır. Kapanış/sonuç gerektirir
- 🔴 **CLV-ONLY** — Sadece CLV analizi ve feedback loop'ta

### 1.2 Feature Kartları

#### F01: `odds_change_72h` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_72h = clean_prob(T_pred) − clean_prob(T72)` |
| **Sezgi** | 3 gün öncesinden ne kadar hareket etti? Büyük erken hareket = büyük bilgi geldi |
| **Tahmin Gerekçesi** | Erken hareket genellikle büyük haber veya sharp para girişi yansıtır |
| **Beklenen Etki** | Düşük-Orta |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F02: `odds_change_48h` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_48h = clean_prob(T_pred) − clean_prob(T48)` |
| **Sezgi** | 2 günlük momentum. T48 genellikle kadrolar açıklanmadan önceki son "sakin" nokta |
| **Beklenen Etki** | Düşük-Orta |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F03: `odds_change_24h` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_24h = clean_prob(T_pred) − clean_prob(T24)` |
| **Sezgi** | Son 24 saat — en kritik "haberli" pencere. Kadro açıklamaları burada yoğunlaşır |
| **Beklenen Etki** | **ORTA-YÜKSEK** — En güçlü tek-pencere odds_change |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F04: `odds_change_12h` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_12h = clean_prob(T_pred) − clean_prob(T12)` |
| **Sezgi** | Yarım gün hareketi — pre-game buildup |
| **Beklenen Etki** | Orta |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F05: `odds_change_6h` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_6h = clean_prob(T_pred) − clean_prob(T6)` |
| **Sezgi** | Son 6 saat — aktif bahis döneminin başlangıcı. Sharp para genellikle T-6h civarında akar |
| **Beklenen Etki** | **ORTA-YÜKSEK** |
| **Leakage Riski** | ⚠️ ORTA — T_pred = T6 ise bu feature 0 olur |

#### F06: `odds_change_1h` — 🟢 KOŞULLU

| Alan | Detay |
|------|-------|
| **Formül** | `odds_change_1h = clean_prob(T_pred) − clean_prob(T1)` |
| **Sezgi** | Son 1 saat — en yüksek bilgi yoğunluğu |
| **Beklenen Etki** | **YÜKSEK** (mevcut olduğunda) |
| **Leakage Riski** | ⚠️ **YÜKSEK** — Tahmin T-6h'de yapılıyorsa T-1h mevcut değil. Koşullu: sadece T_pred ≤ T1 ise |

#### F07: `steam_move_score` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `steam = Σ(strength_i × recency_decay_i)` burada `strength = min(1.0, (|ΔP|/0.03) × (300/span_sec))`, `decay = exp(−0.5 × hours_since)` |
| **Sezgi** | Sharp bahisçiler aniden hareket ederse soft bahisçiler takip eder. Bilgi asimetrisi göstergesi |
| **Tahmin Gerekçesi** | Steam move'lar tarihsel olarak %58-62 doğruluk (rastgele %33) |
| **Beklenen Etki** | **YÜKSEK** — En güçlü tek market sinyali |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F08: `reverse_line_movement` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `RLM = sign(public_direction) × (−1) × sign(odds_direction)`. +1 = RLM tespit, 0 = yok, −1 = aynı yön |
| **Sezgi** | Kamuoyu bir takımı favori görüyor ama odds ters gidiyor → akıllı para diğer tarafta |
| **Beklenen Etki** | **ORTA-YÜKSEK** |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F09: `market_consensus_score` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `consensus = 1.0 − mean(std_home, std_draw, std_away)`. 1 = tam uyum, 0 = tam anlaşmazlık |
| **Sezgi** | Tüm bahisçiler aynı fikirde → piyasa güçlü sinyal veriyor |
| **Beklenen Etki** | Orta — Confidence modifier olarak daha etkili |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F10: `bookmaker_disagreement` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `disagreement = |avg_sharp_prob − avg_soft_prob|` |
| **Sezgi** | Sharp-soft ayrışması → sharp genellikle haklı. Divergence > 3% → %56-60 doğru |
| **Beklenen Etki** | **ORTA-YÜKSEK** |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F11: `sharp_money_signal` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `sharp_signal = Σ(τ_b × clean_prob_b) / Σ(τ_b)` burada b ∈ sharp_bookmakers |
| **Sezgi** | Sharp bahisçilerin ağırlıklı ortalama görüşü — piyasanın "akıllı" kısmı |
| **Beklenen Etki** | **YÜKSEK** — Model kalibrasyonu için en değerli tek sinyal |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F12: `public_money_signal` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `public_signal = Σ(τ_b × clean_prob_b) / Σ(τ_b)` burada b ∈ soft_bookmakers |
| **Sezgi** | Kamuoyunun para yatırdığı taraf |
| **Beklenen Etki** | Düşük (tek başına). Orta (RLM ve disagreement ile) |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F13: `liquidity_proxy` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `liquidity = (book_count / max_count) × (1.0 / avg_overround) × league_factor` |
| **Sezgi** | Likit piyasalar daha verimli fiyatlanır. EPL'de 30 bahisçi + %2 overround → çok verimli |
| **Beklenen Etki** | Düşük (doğrudan). Orta (confidence modifier) |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F14: `market_shock_score` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `shock = max(|ΔP_w|) / median(|ΔP_w|)`. shock > 3.0 → piyasa şoku |
| **Sezgi** | Normal hareketten 3× büyük hareket = şok. Genellikle büyük haber yansıması |
| **Beklenen Etki** | Düşük-Orta (nadir ama güçlü) |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F15: `volatility_score` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `volatility = std([clean_prob(T72), ..., clean_prob(T_pred)])` |
| **Sezgi** | Yüksek volatilite = piyasa kararsız, düşük = erken fiyatlanmış |
| **Beklenen Etki** | Orta |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F16: `closing_line_distance` — 🟡 TRAINING-ONLY

| Alan | Detay |
|------|-------|
| **Formül** | `CLD = model_prob − clean_prob_closing` |
| **Sezgi** | Model kapanışa ne kadar yakın? Pozitif CLD = kapanışın doğru tarafı |
| **Beklenen Etki** | YÜKSEK (eğitim kalitesi) |
| **Leakage Riski** | 🔴 **KRİTİK** — Kapanış odds'u tahmin zamanında YOK. Asla prediction feature OLMAMALI |

#### F17: `bookmaker_trust_weight` — 🟢 PREDICTION-SAFE

| Alan | Detay |
|------|-------|
| **Formül** | `trust = 0.50×opening_accuracy + 0.30×overround_consistency + 0.20×reaction_speed` |
| **Sezgi** | Pinnacle'ın güveni Bet365'ten yüksek çünkü daha verimli fiyatlıyor |
| **Beklenen Etki** | Orta — Diğer feature'ların ağırlığı olarak |
| **Leakage Riski** | ⚠️ DÜŞÜK |

#### F18: `line_efficiency_score` — 🟡 TRAINING-ONLY

| Alan | Detay |
|------|-------|
| **Formül** | `efficiency = 1.0 − |clean_prob_closing − actual_outcome_prob|` |
| **Sezgi** | Piyasanın bu maçı ne kadar verimli fiyatladığı |
| **Beklenen Etki** | Orta (regime detection input) |
| **Leakage Riski** | 🔴 **KRİTİK** — Maç sonucu + kapanış gerektirir |

---

### 1.3 Özet Tablosu

| # | Feature | Sınıf | Etki | Leakage |
|---|---------|-------|------|---------|
| F01 | odds_change_72h | 🟢 | Düşük-Orta | Düşük |
| F02 | odds_change_48h | 🟢 | Düşük-Orta | Düşük |
| F03 | odds_change_24h | 🟢 | **Orta-Yüksek** | Düşük |
| F04 | odds_change_12h | 🟢 | Orta | Düşük |
| F05 | odds_change_6h | 🟢 | **Orta-Yüksek** | Orta |
| F06 | odds_change_1h | 🟢* | **Yüksek** | **Yüksek** |
| F07 | steam_move_score | 🟢 | **Yüksek** | Düşük |
| F08 | reverse_line_movement | 🟢 | **Orta-Yüksek** | Düşük |
| F09 | market_consensus_score | 🟢 | Orta | Düşük |
| F10 | bookmaker_disagreement | 🟢 | **Orta-Yüksek** | Düşük |
| F11 | sharp_money_signal | 🟢 | **Yüksek** | Düşük |
| F12 | public_money_signal | 🟢 | Düşük | Düşük |
| F13 | liquidity_proxy | 🟢 | Düşük | Düşük |
| F14 | market_shock_score | 🟢 | Düşük-Orta | Düşük |
| F15 | volatility_score | 🟢 | Orta | Düşük |
| F16 | closing_line_distance | 🟡 | Yüksek | **KRİTİK** |
| F17 | bookmaker_trust_weight | 🟢 | Orta | Düşük |
| F18 | line_efficiency_score | 🟡 | Orta | **KRİTİK** |

---

## 2. Market Signal Engine

6 adımlı pipeline:

1. **SNAPSHOT RETRIEVAL** — match_id → tüm odds_snapshots, bookmaker sharp/soft gruplama
2. **WINDOW ALIGNMENT** — Her snapshot → en yakın standart pencereye (tolerans dahilinde)
3. **PROBABILITY EXTRACTION** — Pencere × bookmaker → clean_prob. Eksik → NULL (interpolasyon YOK)
4. **FEATURE COMPUTATION** — T_pred tanımı + 18 feature hesaplama
5. **CONFIDENCE ATTACHMENT** — Her feature'a feature_confidence eşlik eder
6. **LEAKAGE GUARD** — Eğitim/tahmin ayrımı, available_at_prediction flag kontrolü

---

## 3. Feature Fusion Layer

**58 feature** = 40 mevcut + 16 prediction-safe + 2 training-only

- **NULL Handling:** LightGBM native NULL desteği + confidence weighting
- **Confidence Weighting:** feature_value × confidence_score (scaled feature)
- **Feature Importance Feedback:** SHAP analizi → düşük importance → disable

---

## 4. CLV Learning Inputs

4 CLV bileşeni: clv_pct, clv_vs_sharp, clv_vs_soft, clv_vs_market_avg

Feature-CLV korelasyon analizi (aylık feedback):
- steam ↔ clv: ρ ≈ 0.25-0.35
- sharp_signal ↔ clv: ρ ≈ 0.30-0.40
- odds_change_24h ↔ clv: ρ ≈ 0.15-0.25
- disagreement ↔ |clv|: ρ ≈ 0.20-0.30

---

## 5. Market Reliability Scoring

`market_reliability = 0.30×liquidity + 0.25×consensus + 0.25×snapshot_coverage + 0.20×freshness`

- < 0.3 → MIW feature'lar devre dışı (sadece takım-bazlı tahmin)
- 0.3-0.8 → Lineer ölçekleme
- \> 0.8 → Tam MIW entegrasyonu

---

## 6. Signal Decay Logic

| Sinyal | Decay | Yarı-ömür |
|--------|-------|-----------|
| Steam (F07) | exp(−0.5 × hours) | ~1.4 saat |
| RLM (F08) | Binary — decay yok | — |
| Consensus (F09) | Stabilize (artış) | — |
| Stale penalty | exp(−0.2 × hours) | ~3.5 saat |
| > 12h stale | Tüm MIW → NULL | — |

---

## 7. Market Regime Detection

3 rejim (30 gün kayan pencere, lig bazlı):

| Rejim | Tespit | Etki |
|-------|--------|------|
| **0: Efficient** | avg(efficiency) > 0.65 | Market ağırlığı ↑ (EPL, Bundesliga) |
| **1: Transitional** | 0.45-0.65 | Dengeli blend (La Liga, Serie A) |
| **2: Inefficient** | < 0.45 | Model ağırlığı ↑ (Süper Lig, küçük ligler) |

---

## 8. League-Specific Signal Adjustments

8 lig profili × feature ağırlık çarpanları:

| Feature | EPL | Bundesliga | Süper Lig | Default |
|---------|-----|-----------|-----------|---------|
| steam | 1.20 | 1.15 | 0.50 | 0.80 |
| sharp_signal | 1.20 | 1.15 | 0.60 | 0.90 |
| odds_change_* | 1.00 | 1.00 | 0.70 | 0.85 |
| volatility | 0.90 | 0.90 | 1.10 | 1.00 |

Çarpanlar 90 günlük CLV korelasyonundan öğrenilir.

---

## 9. Signal Hierarchy

| Tier | Feature'lar | Önem |
|------|------------|------|
| **S (Kritik)** | sharp_money_signal, steam_move_score | ★★★★★ |
| **A (Yüksek)** | odds_change_24h, RLM, disagreement, odds_change_6h | ★★★★ |
| **B (Orta)** | consensus, volatility, trust_weight, odds_change_12h | ★★★ |
| **C (Bağlamsal)** | odds_change_72h/48h/1h, public, liquidity, shock | ★★ |
| **T (Training)** | closing_line_distance, line_efficiency | ★★★★★ (CLV) |

---

## 10. Implementation Roadmap (11 Faz, 13 Hafta)

| Faz | Hafta | İçerik |
|-----|-------|--------|
| S1 | 1-2 | Odds Change Features (F01-F06) + leakage guard |
| S2 | 3-4 | Sharp/Soft Signals (F11, F12, F10, F17) |
| S3 | 4-5 | Event Signals (F07 steam, F08 RLM, F14 shock) |
| S4 | 5-6 | Market Meta (F09, F13, F15) |
| S5 | 6-7 | Training Features (F16, F18) + leakage proof test |
| S6 | 7-8 | Signal Engine pipeline birleştirme |
| S7 | 8-9 | Fusion Layer (58-dim feature vector) |
| S8 | 9-10 | Regime Detection + League Adjustments |
| S9 | 10-11 | Signal Decay + Market Reliability |
| S10 | 11-12 | CLV Integration + feedback loop |
| S11 | 12-13 | Validation (A/B test, leakage audit, backtest) |

Kritik yol: S1 → S2 → S3 → S6 → S7 → S11
