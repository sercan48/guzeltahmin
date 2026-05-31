# Orkestrasyon Planı: Premium Telegram Bahis Botu & Kendi Kendini İyileştiren Sistem

## Mimarinin Amacı (Goal)
Sistemi sadece bir analiz aracı olmaktan çıkarıp, Telegram'da premium bir gruba (veya kanala) hizmet edecek "Yüksek İsabetli, Sürekli Öğrenen, Kupon Üreten" otonom bir robota dönüştürmek. Model, her hafta maç sonuçlarını analiz ederek (Feedback Loop) kendi hatalarından ders çıkaracak.

---

## 🛑 Socratic Gate (Cevaplanması Gereken Analiz Soruları)
Projeyi kodlamaya başlamadan önce `project-planner` olarak şu stratejik noktaları netleştirmemiz gerekiyor:

1. **Telegram Entegrasyonu**: Bot doğrudan belirlediğin Telegram kanalına (otomatik mesaj olarak) kuponları kendi mi atsın, yoksa sen sadece `streamlit_app.py` üzerinden kopyalayıp kendin mi atacaksın?
2. **Kupon Dağılım Stratejisi**: Haftalık olarak "Banko Kupon (2-3 maç)", "Sürpriz Sistem (TGS vb.)" ve "Günün Teklisi" gibi belirli bir şablon mu izleyelim?
3. **Veri / API İhtiyacı Beyanı**: Şu an kullandığımız ücretsiz GitHub verisi ve RapidAPI (Canlı Maç Skoru) iyi ancak *sakatlıklar, hakem eylemleri ve xG* gibi premium metriklerde %90 isabet oranını garantilemek için ileride **Sportmonks** veya **API-Football (Ücretli Tier)** ihtiyacımız olabilir. Şimdilik bedava sınırlarında `prediction_verifier.py` isimli otonom geri bildirim sistemini (Feedback Loop) kurarak ML Algoritmasını kendi hatalarından öğrenen bir "Random Forest / XGBoost" auto-tuner modeline çevirmemi onaylıyor musun?

---

## 🎼 Orkestrasyon Aşamaları (Task Breakdown)

### 1. ALGORİTMA & VERİ TABANI GELİŞTİRME (Agent: `backend-specialist` & `database-architect`)
*   **Aksiyon**: Yeni bir `ml_feedback_loop.py` yazılacak. Bu betik, gerçekleşen sonuçlar ile tahminleri karşılaştırıp, modeldeki "Özelliklerin (Features) Ağırlıklarını" yeniden eğitecek.
*   **Amaç**: "%94 dedik ama 2-2 bitti" hatalarından bir daha yapmamak için modelin ağırlıklarını cezalandırma yoluyla otonom eğitebilmesi.

### 2. PREMIUM KUPON ÜRETİCİSİ (Agent: `backend-specialist`)
*   **Aksiyon**: `coupon_builder.py` içerisine Telegram "Copy-Paste" dostu emoji ve açıklamalarla dolu yeni bir şablon eklenecek.
*   **Amaç**: "Günün Bankosu", "Hafta Sonu Katlaması", "Değerli Bahisler (Arbitraj)" isimleriyle birbirinden bağımsız 3 farklı kupon çıkaran sistem.

### 3. SİSTEM TESTİ VE BACKTESTING (Agent: `test-engineer`)
*   **Aksiyon**: `[/test]` komutu direktifi doğrultusunda, modelin 2024-2025 sezonu verileri üzerinden binlerce maçı simüle ederek isabet oranını (Hit Rate) matematiksel olarak kanıtlayacak `test_runner.py` çalıştırılacak.
*   **Amaç**: Oran analizi doğruluğunun gerçekten %80+ olduğunu ispatlamak.

---

## 🔴 ORCHESTRATOR EXIT GATE
Bu doküman `/orchestrate` komutu protokolünün *PHASE 1 (Planning)* aşamasıdır.

Onaylıyor musunuz? (Y/N)
- **Y**: `backend-specialist`, `database-architect` ve `test-engineer` ajanları eşzamanlı olarak Implementation (Uygulama) fazına geçecektir.
- **N**: Planı istekleriniz doğrultusunda düzeltirim.
