# MIW Faz 6 — CLV Intelligence & Market Learning Architecture

> **Version 6.0** — Market Intelligence Warehouse: Intelligence & Market Learning Layer
> **Bağımlılıklar:** MIW_ARCHITECTURE.md (Faz 1) · MIW_DATABASE_LAYER.md (Faz 2) · MIW_INGESTION_ARCHITECTURE.md (Faz 3) · MIW_COLLECTOR_BACKFILL.md (Faz 4) · MIW_FEATURE_LAYER.md (Faz 5)
> **İlgili motorlar:** CLV_ENGINE.md · THRESHOLD_SYSTEM.md · MODEL_STACK.md · PLAN-confidence-calibration.md
> **Durum:** Tasarım — implementasyon öncesi
> **DO NOT WRITE CODE** — Bu doküman saf mimari ve matematiksel tasarımdır.

---

## 1. Executive Summary

Faz 1-5, ham odds verisini toplayıp (ingestion + backfill), temizleyip (clean_prob, overround), 18 piyasa feature'ına dönüştüren bir **Market Intelligence Warehouse** kurdu. Faz 6, bu altyapının üzerine **öğrenen zeka katmanını** ekler: odds geçmişini *piyasa öğrenmesine* çeviren 8 motor.

Temel tez: **CLV (Closing Line Value), uzun vadeli ROI'nin en güçlü öncü göstergesidir.** Bir bahis kapatılmadan önce bile, kapanış çizgisini geçip geçmeyeceğimizi tahmin edebilirsek (expected_clv), kararları sonuç gerçekleşmeden optimize edebiliriz. Faz 6 bunu yapısal hale getirir:

1. **CLV Engine v2** — CLV'yi tek bir yüzde olmaktan çıkarıp 5 boyutlu bir kalite vektörüne dönüştürür (beklenen, gerçekleşen, tutarlılık, kararlılık, güven).
2. **Market Efficiency Engine** — Lig, bahisçi ve konsensüs kapanış fiyatlarının ne kadar "doğru" olduğunu ölçer.
3. **Sharp Money Detection Engine** — Akıllı para hareketini (steam, koordineli hamle, RLM) tespit eder ve tek bir sharp_confidence skoruna yoğunlaştırır.
4. **Market Trust Engine** — Bahisçi / piyasa / lig bazında "ne kadar güvenebiliriz" skorları üretir.
5. **Closing Price Predictor** — Açılış odds + hareket + hız + volatiliteden beklenen kapanış fiyatını tahmin eder (expected_clv'nin motoru).
6. **Market Learning Layer** — Piyasa davranışını, piyasa hatalarını, bahisçi ve lig önyargılarını sürekli öğrenir.
7. **Market Regime Detection** — Piyasayı 2×2 (verimli/verimsiz × stabil/volatil) rejime ayırır.
8. **Decision Engine Integration** — Tüm çıktıları Prediction / Calibration / Threshold / CLV katmanlarına bağlar.

Kritik tasarım ilkesi (Faz 5'ten miras): **sıkı leakage guard.** Kapanış türevli her büyüklük (realized_clv, line_efficiency) yalnızca *eğitim/öğrenme* aşamasında kullanılır; tahmin zamanında yalnızca öncü, kapanış-öncesi sinyaller (expected_clv dahil) kullanılabilir.

---

## 2. Architecture

### 2.1 Katman görünümü

Faz 6, Faz 5'in Feature Layer çıktısını ve Faz 2'nin warehouse tablolarını okur; yeni "learning" tabloları yazar; çıktısını Karar Motoruna besler.

```
  WAREHOUSE (Faz 2)                  FEATURE LAYER (Faz 5)
  odds_snapshots                     18 market feature + confidence
  closing_lines                      market_reliability, regime(3)
  market_movements ─┐                          │
  steam_moves       │                          │
  market_consensus  │                          ▼
  clv_history       │        ╔═══════════════════════════════════╗
  value_edges       └───────▶║      FAZ 6 — INTELLIGENCE LAYER    ║
                             ║                                   ║
                             ║  E1 CLV Engine v2                 ║
                             ║  E2 Market Efficiency Engine      ║
                             ║  E3 Sharp Money Detection Engine  ║
                             ║  E4 Market Trust Engine           ║
                             ║  E5 Closing Price Predictor       ║
                             ║  E6 Market Learning Layer ◀──┐    ║
                             ║  E7 Market Regime Detection  │    ║
                             ╚══════════════╤═══════════════│════╝
                                            │  feedback ────┘
                                            ▼
              ╔═════════════════════════════════════════════════╗
              ║   E8 DECISION ENGINE INTEGRATION                ║
              ║   Prediction · Calibration · Threshold · CLV    ║
              ╚═════════════════════════════════════════════════╝
```

### 2.2 Yeni warehouse tabloları (Faz 6)

Mevcut şemayı (Faz 2) bozmadan, ayrı `guzel_tahmin_miw.db` içine eklenir:

| Tablo | Amaç | Yazma kadansı | Hacim |
|-------|------|---------------|-------|
| `clv_metrics` | Rolling expected/realized CLV + consistency/stability/confidence (lig×market×selection) | Kickoff reconciliation + günlük | Düşük |
| `market_efficiency` | league/bookmaker/market efficiency skorları (rolling 30/90g) | Günlük cron | Düşük |
| `trust_scores` | bookmaker/market/league trust snapshot | Günlük + olay | Düşük |
| `closing_predictions` | Tahmini kapanış prob (q10/q50/q90) per match×selection×window | Snapshot anında | Orta |
| `market_regime_state` | Lig bazlı 2×2 rejim + histeri durumu, versiyonlu | Günlük | Çok düşük |
| `bookmaker_bias` | Favori-longshot ve sonuç bazlı sistematik önyargı vektörleri | Haftalık | Düşük |
| `league_bias` | Lig bazlı önyargı (örn. beraberlik düşük fiyatlama) | Haftalık | Çok düşük |
| `market_error_log` | Piyasanın yanıldığı maçların kaydı (öğrenme girdisi) | Settlement | Orta |

`clv_history` (Faz 2) genişletilir: `expected_clv`, `realized_clv_prob`, `clv_consistency_snapshot`, `clv_confidence` kolonları eklenir.

### 2.3 8 motorun bağımlılık sırası

```
E5 Closing Price Predictor ──▶ E1 CLV Engine v2 (expected_clv)
E2 Market Efficiency ─┬─▶ E4 Market Trust ─┬─▶ E7 Regime ─▶ E8 Integration
E3 Sharp Detection ───┘                    │
E1 + E2 + E3 + E4 ────────▶ E6 Learning Layer ──(feedback)──▶ E2,E4,E7,E8
```

Kritik yol: **E5 → E1 → E8** (expected_clv kapısı) ve **E2 → E4 → E7 → E8** (rejim-bazlı harmanlama).

---

## 3. Mathematical Definitions

Notasyon: $P^{entry}$ = oynanan seçimin tahmin anındaki temiz (marj-temizlenmiş) olasılığı; $P^{close}$ = kapanış temiz olasılığı; $o$ = ondalık oran; $y_k \in \{0,1\}$ = sonuç; $N$ = rolling pencere örnek sayısı.

### 3.1 Engine 1 — CLV Engine v2

Mevcut v1 (CLV_ENGINE.md) CLV'yi odds-uzayında tek bir yüzde olarak tanımlıyordu. v2 bunu **olasılık-uzayına** taşır (işaret tutarlılığı için kanonik) ve 5 boyuta genişletir.

**Realized CLV (kanonik, olasılık-uzayı):**
$$
\text{realized\_clv} = \frac{P^{close}_{clean} - P^{entry}_{clean}}{P^{entry}_{clean}}
$$
Pozitif ⇒ piyasa oynadığımız seçime doğru hareket etti ⇒ kapanışı geçtik.

**Odds-uzayı eşdeğeri (v1 ile uzlaştırma):**
$$
\text{realized\_clv}^{odds} = \frac{o_{entry} - o_{close}}{o_{close}}
$$
Not: v1'deki $(o_{close}-o_{pred})/o_{pred}$ işaret olarak terstir; v2 kanonik tanım olasılık-uzayıdır, raporlama için odds-uzayı türetilir.

**Expected CLV (settlement öncesi, E5'ten):**
$$
\text{expected\_clv} = \frac{\hat{P}^{close}_{clean} - P^{entry}_{clean}}{P^{entry}_{clean}}
$$

**CLV Consistency (kapanışı geçme oranı, rolling N):**
$$
\text{clv\_consistency} = \frac{1}{N}\sum_{i=1}^{N}\mathbb{1}\big[\text{realized\_clv}_i > 0\big]
$$

**CLV Stability (düşük varyans = yüksek kararlılık):**
$$
\text{clv\_stability} = \frac{1}{1 + \sigma_N(\text{realized\_clv})}
$$

**CLV Confidence (edge gerçek mi? t-istatistiği × örnek küçülmesi):**
$$
t = \frac{\overline{\text{realized\_clv}}}{\sigma_N / \sqrt{N}}, \qquad
\text{clv\_confidence} = \text{logistic}(t)\cdot\frac{N}{N+k}, \quad k\approx 20
$$

### 3.2 Engine 2 — Market Efficiency Engine

Faz 5 F18 (`line_efficiency_score = 1 - |clean_prob_closing - actual_outcome_prob|`) tek maç içindi; burada lig/bahisçi/piyasa düzeyinde toplulaştırılır. Baseline = üniform 1X2 Brier $BS_0 = 0.667$.

**League Efficiency Score:**
$$
BS^{close}_L = \frac{1}{N_L}\sum_{m\in L}\sum_{k}\big(P^{close}_{m,k}-y_{m,k}\big)^2, \qquad
\text{league\_efficiency} = \text{clip}\!\left(1-\frac{BS^{close}_L}{BS_0},\,0,\,1\right)
$$

**Bookmaker Efficiency Score (doğruluk + overround sıkılığı):**
$$
\text{bookmaker\_efficiency}_b = 0.7\left(1-\frac{BS^{close}_b}{BS_0}\right) + 0.3\left(1-\frac{\overline{overround_b}-1}{\kappa}\right), \quad \kappa\approx 0.10
$$

**Market Accuracy Score (konsensüs kapanış, log-loss bazlı):**
$$
\text{market\_accuracy} = \text{clip}\!\left(1-\frac{\text{LogLoss}^{close}_{consensus}}{\text{LogLoss}_0},\,0,\,1\right), \quad \text{LogLoss}_0=\ln 3
$$

### 3.3 Engine 3 — Sharp Money Detection Engine

**Steam strength (Faz 5 F07 ile uyumlu):**
$$
s = \min\!\left(1,\; \frac{|\Delta P|}{0.03}\cdot\frac{300}{\Delta t_{sec}}\right)
$$

**Coordinated bookmaker move (W penceresinde, baskın yön $d$):**
$$
\text{coord} = \frac{1}{B}\sum_{b=1}^{B}\mathbb{1}\big[\text{sign}(\Delta P_b)=d \,\wedge\, |\Delta P_b|\ge \tau\big]\cdot \omega_b, \quad \tau=0.01
$$
$\omega_b$ = bahisçi sharp-ağırlığı (trust'tan).

**Reverse Line Movement (büyüklüklü):**
$$
\text{rlm} = \mathbb{1}\big[\text{sign}(\text{public\_dir}) \ne \text{sign}(\Delta o)\big]\cdot |\Delta P|
$$

**Sharp Confidence Score:**
$$
\text{sharp\_confidence} = \text{logistic}\!\big(\alpha_1 s + \alpha_2\,\text{coord} + \alpha_3\,\text{rlm} + \alpha_4\,\text{disagreement}\big)
$$
$\text{disagreement}=|avg\_sharp\_prob - avg\_soft\_prob|$ (F10). $\alpha$ ağırlıkları E6 tarafından CLV korelasyonundan öğrenilir.

### 3.4 Engine 4 — Market Trust Engine

**Bookmaker Trust (Faz 5 F17'yi gerçekleşen doğruluk + steam liderliğiyle genişletir):**
$$
\text{bk\_trust} = 0.35\,acc^{close}_b + 0.25\,opening\_accuracy_b + 0.20\,overround\_consistency_b + 0.10\,reaction\_speed_b + 0.10\,steam\_leadership_b
$$

**Market Trust (maç×pencere; market_reliability'yi trust ile genişletir):**
$$
\text{mkt\_trust} = 0.30\,liquidity + 0.25\,consensus + 0.20\,coverage + 0.15\,freshness + 0.10\,\overline{bk\_trust}
$$

**League Trust:**
$$
\text{lg\_trust} = 0.50\,\text{league\_efficiency}_L + 0.30\,\overline{liquidity}_L + 0.20\,\rho(\text{signal},\text{clv})_L
$$

### 3.5 Engine 5 — Closing Price Predictor

**Hedef:** $P^{close}_{clean}$ (seçim bazlı). **Girdiler (hepsi kapanış-öncesi, leakage-safe):** $P^{open}$, odds_change_{72,48,24,12,6}, velocity, acceleration, volatility_score, steam_move_score, market_consensus_score, bookmaker_count, time_to_kick, market_regime, league_code.

**Model:** Gradient boosted regresör (LightGBM) + **quantile başlıkları** (q10/q50/q90) — pinball loss:
$$
\mathcal{L}_q(y,\hat y) = \max\big(q(y-\hat y),\,(q-1)(y-\hat y)\big)
$$

**Çıktı:** $\hat P^{close}=q_{50}$; oran $\hat o^{close}=1/\hat P^{close}$ (gerekirse yeniden marjlanır). Belirsizlik genişliği $\Delta=q_{90}-q_{10}$ → expected_clv güven aralığı ve clv_confidence girdisidir.

### 3.6 Decision Score v2 (E8'in çekirdeği)

$$
\text{DS}_2 = \text{ProbScore} + \text{ValueScore} + \beta_1\,\text{expected\_clv} + \beta_2\,\text{sharp\_confidence} + \text{CLVHistoryScore} + \text{MarketBiasAdj} + \text{RegimeAdj} - \text{RiskPenalty} - \beta_3(1-\text{mkt\_trust})
$$

**Triple-gate PLAY kuralı (mevcut double-gate'i genişletir):**
$$
\text{Edge}\ge 2\% \;\wedge\; P_{cal}\ge T_{adj} \;\wedge\; \text{expected\_clv}\ge \tau_{clv}(\text{regime})
$$

---

## 4. Learning Framework (Engine 6 — Market Learning Layer)

Piyasa öğrenmesi dört paralel döngüde gerçekleşir; tümü `market_error_log` ve `clv_metrics` üzerinden beslenir.

### 4.1 Piyasa davranışı nasıl öğrenilir
Her lig×rejim için açılış→kapanış sürüklenme dağılımı $D_{L,r}(\Delta P)$ rolling olarak kestirilir. Bu dağılım Closing Price Predictor'a prior, regime detection'a girdi olur. Yeni maçlar geldikçe üstel ağırlıklı güncelleme (EWMA, yarı-ömür ~90 gün).

### 4.2 Piyasa hataları nasıl izlenir
Kapanış olasılığı yüksek olup kaybeden maçlar `market_error_log`'a yazılır. Lig/bahisçi bazlı **market_error_rate** Bayesyen güncellenir (Beta-Binomial). Yüksek hata → efficiency ↓, trust ↓, model ağırlığı ↑.

### 4.3 Bahisçi önyargıları nasıl izlenir
Her bahisçi için favori-longshot bias ve sonuç-bazlı sapma vektörü `bookmaker_bias` tablosunda tutulur:
$$
\text{bias}_b(\text{bin}) = \overline{P^{close}_{b}}(\text{bin}) - \overline{y}(\text{bin})
$$
Prob bin'leri (örn. 0.1 aralık) üzerinden. Bias, consensus füzyonunda debias düzeltmesi olarak uygulanır.

### 4.4 Lig önyargıları nasıl izlenir
Lig bazlı sistematik sapmalar (örn. Süper Lig'de beraberlik düşük fiyatlama) `league_bias`'a yazılır; mevcut **League-Specific Residual Layer** (MODEL_STACK Bölüm 4) için ek düzeltici girdi olur.

### 4.5 Geri besleme kadansı
- **Online (kickoff reconciliation):** realized_clv hesaplanır → SHAP-CLV feature ağırlıkları (CLV_ENGINE Bölüm 4) + sharp_confidence $\alpha$ ağırlıkları mikro-güncellenir.
- **Günlük cron:** efficiency, trust, regime yeniden hesaplanır.
- **Haftalık batch:** bias vektörleri, Closing Price Predictor yeniden eğitimi, Optuna eşik optimizasyonu (THRESHOLD_SYSTEM).

---

## 5. Regime Framework (Engine 7 — Market Regime Detection)

Faz 5'teki 3-rejim (efficient/transitional/inefficient) 2 eksenli **2×2 matrise** genişletilir.

- **Verimlilik ekseni:** league_efficiency_score (eşik ~0.55, histeri ±0.05)
- **Stabilite ekseni:** volatility_score / price_dispersion (eşik lig-bazlı, histeri ±0.05)

| Rejim | Koşul | Politika |
|-------|-------|----------|
| **R_ES** Efficient-Stable | eff ↑, vol ↓ | Piyasaya en çok güven; $w_{market}\approx 0.6\text{-}0.7$; standart eşik |
| **R_EV** Efficient-Volatile | eff ↑, vol ↑ | Piyasa ağırlıklı ama eşik yükselt, stake ↓ |
| **R_IS** Inefficient-Stable | eff ↓, vol ↓ | Modele güven; $w_{market}\approx 0.2\text{-}0.3$ |
| **R_IV** Inefficient-Volatile | eff ↓, vol ↑ | Maksimum ihtiyat; en yüksek eşik, en düşük stake |

**Histeri:** Rejim değişimi için ardışık 3 gün eşik aşımı gerekir (flapping önleme). Durum `market_regime_state` tablosunda versiyonlu tutulur.

**Piyasa-model harman ağırlığı:**
$$
w_{market} = \text{clip}\big(\text{base}(\text{regime})\cdot \text{mkt\_trust}\cdot \text{lg\_trust},\; 0,\; 0.8\big)
$$

---

## 6. Integration Plan (Engine 8 — Decision Engine Integration)

### 6.1 Prediction Layer
Leakage-safe market feature'lar (sharp_money_signal, steam, RLM, disagreement, odds_change_*) zaten Faz 5 feature vektöründe (58-dim). Faz 6 ek olarak **expected_clv** ve Closing Price Predictor q50/aralık genişliğini prediction-safe feature olarak ekler (kapanış kullanılmaz, tahmin edilir).

### 6.2 Calibration Layer
Mevcut ECE-minimizer kalibratör (Platt/Isotonic/Beta) korunur; üstüne **rejim/güven-farkında piyasa harmanı** eklenir:
$$
P^{final} = w_{market}\,P^{close-pred}_{clean} + (1-w_{market})\,P^{model}_{cal}
$$
$w_{market}$ rejim + mkt_trust + lg_trust'tan gelir. Düşük güvende harman devre dışı (saf model).

### 6.3 Threshold Layer
Decision Score v2 ve triple-gate uygulanır. Optuna utility yeniden dengelenir:
$$
\text{Score} = 0.35\,ROI + 0.30\,CLV_{realized} + 0.15\,Edge + 0.10\,\text{clv\_consistency} - 0.10\,\text{CoveragePenalty}
$$
Eşikler rejim-koşullu ($T_{adj}$ rejime göre kayar). Drawdown ≥%15 diskalifiye ve safety rollback (THRESHOLD_SYSTEM Bölüm 4) korunur.

### 6.4 CLV Layer
Kickoff'ta realized_clv hesaplanır → clv_metrics güncellenir → E6 öğrenme döngülerini tetikler. expected_clv, PLAY kapısının üçüncü kapısıdır: pozitif beklenen CLV olmadan oynanmaz.

---

## 7. Implementation Roadmap

| Faz | Hafta | İçerik | Bağımlılık |
|-----|-------|--------|-----------|
| **6.1** | 1-2 | clv_metrics tablo + CLV Engine v2 (5 metrik, prob-uzayı reconcile) | Faz 2 clv_history |
| **6.2** | 2-4 | Closing Price Predictor (quantile LightGBM, leakage audit) | 6.1 |
| **6.3** | 4-5 | expected_clv üretimi + prediction-safe feature entegrasyonu | 6.2 |
| **6.4** | 5-6 | Market Efficiency Engine (league/bookmaker/market) | Faz 2 closing_lines |
| **6.5** | 6-7 | Sharp Money Detection Engine (coord + sharp_confidence) | Faz 2 steam_moves |
| **6.6** | 7-8 | Market Trust Engine (3 trust skoru) | 6.4, 6.5 |
| **6.7** | 8-9 | Market Regime Detection (2×2 + histeri) | 6.4, 6.6 |
| **6.8** | 9-11 | Market Learning Layer (4 döngü, bias tabloları) | 6.1-6.7 |
| **6.9** | 11-12 | Decision Engine Integration (DS v2, triple-gate, harman) | tümü |
| **6.10** | 12-14 | Validation (backtest, leakage audit, A/B, drawdown) | 6.9 |

**Kritik yol:** 6.1 → 6.2 → 6.3 → 6.9 (expected_clv kapısı) ve 6.4 → 6.6 → 6.7 → 6.9 (rejim harmanı).

---

## 8. Risks

| # | Risk | Etki | Azaltma |
|---|------|------|---------|
| R1 | **Leakage** — kapanış türevli büyüklüklerin tahmine sızması | Şişirilmiş backtest, canlıda çöküş | Eğitim/tahmin ayrımı, available_at_prediction flag, otomatik leakage audit (Faz 5 S5) |
| R2 | **Circular learning** — model piyasayı kopyalayan bir piyasadan öğrenir | Sahte özgüven, çeşitlilik kaybı | Inefficient rejimde model ağırlığı ↑; sharp-only sinyaller; bias debias |
| R3 | **Market regime shift** — tarihsel rejim canlıda geçersiz | Yanlış harman ağırlığı | Histeri + rolling pencere + drift monitor (mevcut 3-seviye) |
| R4 | **Küçük lig sparsity** — yetersiz örnek | Güvenilmez efficiency/trust | clv_confidence örnek-küçülmesi, market_reliability<0.3 → MIW kapalı |
| R5 | **Overfitting** — Closing Price Predictor ezberler | Kötü genelleme | Quantile + cross-val + lig-bazlı regularization |
| R6 | **CLV–Edge çoklu-doğrusallık** | Kararsız ağırlıklar | Decision Score'da ortogonalizasyon, SHAP izleme |
| R7 | **Provider outage / stale odds** | Eksik sinyal | Faz 3 circuit breaker + signal decay (>12h → NULL) |
| R8 | **Capital risk** — agresif stake | Drawdown | Drawdown ≥%15 diskalifiye + safety rollback |

---

## 9. Expected ROI Impact

Doğrudan tek bir motor değil, **karar kalitesi çarpanı** olarak etki eder. Tahmini (lig ve örnek bağımlı, kesin garanti değil):

| Mekanizma | Beklenen etki | Gerekçe |
|-----------|---------------|---------|
| **expected_clv kapısı** (triple-gate) | ROI **+2-5 puan**, negatif-CLV bahislerin elenmesi | CLV uzun-vade ROI öncü göstergesi; pozitif CLV portföyü tarihsel olarak +EV |
| **Rejim-bazlı harman** | Verimsiz liglerde edge korunur, verimli liglerde varyans ↓ | Doğru ligde doğru kaynağa ağırlık |
| **Sharp confidence** | Hit-rate **+1-3 puan** yüksek-güven seçimlerde | Steam/sharp tarihsel %56-62 doğruluk (Faz 5 F07/F10) |
| **Market trust filtresi** | Düşük güvenli piyasalarda yanlış-pozitif ↓ | Güvenilmez konsensüsten kaçınma |
| **clv_consistency utility** | Daha kararlı bankroll eğrisi, drawdown ↓ | Optuna artık tutarlılığı da ödüllendirir |

**Net beklenti:** Mevcut Production Readiness 74/100 sisteminde, Faz 6 ana katkısı *ham ROI artışından çok* **risk-ayarlı getiri** (daha yüksek CLV-tutarlılığı, daha düşük drawdown, daha seçici ama daha doğru kuponlar) olacaktır. Asıl kazanç, kararların sonuç gerçekleşmeden önce kapanış çizgisiyle hizalanmasıdır.

---

*Bu doküman tasarımdır; kod içermez. Implementasyon Roadmap (Bölüm 7) sırasına göre ilerlenmelidir.*
