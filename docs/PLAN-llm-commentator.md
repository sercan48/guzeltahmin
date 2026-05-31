# Orkestrasyon Planı: LLM Destekli "Sports Analyst" Entegrasyonu

## Mimarinin Amacı (Goal)
Kupon sisteminin ürettiği ham matematiksel verileri (XGBoost algoritma sonuçlarını, sakatlıkları, xG rakamlarını), bir Büyük Dil Modeline (LLM - ChatGPT / Gemini) göndererek;
1. Sayıların gerçek hayatta ne anlama geldiğini organik olarak analiz ettirmek.
2. Sadece sonuç veren değil, tıpkı birinci sınıf bir spor danışmanı gibi güven veren "Gerekçeli ve Emosyonel" Telegram gönderileri yazdırmak.

---

## 🛑 Socratic Gate (Cevaplanması Gereken Analiz Soruları)
Projeyi hızla kodlamaya başlamadan önce `project-planner` olarak altyapı gereksinimlerimizi belirlemeliyiz:

1. **Hangi LLM API'sini Kullanacağız?**
   - **Google Gemini API**: Genelde ücretsiz bir başlangıç kotası (Free Tier) verir, entegrasyonu kolaydır.
   - **OpenAI API (GPT-4o vb.)**: Mantıksal analizde rakipsizdir ama kullanım başına cüzi de olsa kredi/bakiye gerektirir.
   _Hangisinin API Key'ini temin edebilirsin veya halihazırda var_?

2. **Yapay Zekanın İzin Seviyesi (Otorite)**
   - **Seviye 1 (Sadece Yazar)**: Bizim XGBoost algoritmamız kuponu kesin oluşturur. LLM sadece bu kararı süsleyerek mantıklı bir Telegram gönderisi yazar.
   - **Seviye 2 (Veto Hakkı - Karar Verici)**: Algoritma maçları bulur, LLM'e sorar. LLM der ki: *"Bu maçta ev sahibinin ciddi teknik direktör krizi var, XGBoost bunu bilemez, bu maçı kupondan VETO ediyorum."*
   _Sence YZ'ye sadece yazarlık mı verelim, yoksa son kararı vereceği bir Veto hakkı da tanıyalım mı?_

---

## 🎼 Orkestrasyon Aşamaları (Task Breakdown)

### 1. ALTYAPI & BAĞLANTI (Agent: `backend-specialist`)
*   **Aksiyon**: Yeni bir `src/ingestion/llm_client.py` yazılacak. Gemini veya OpenAI kütüphaneleri kullanılarak sisteme güvenli API bağlantısı sağlanacak.
*   **Prompt Engineering**: Sistemin arka planında LLM'e şu prompt verilecek: *"Sen elit ve %90 isabet oranına sahip Telegram Premium grubuna kupon paylaşan bir danışmansın. Sana aşağıdaki istatistikleri veriyorum, bana kupon yorumu yaz."*

### 2. KUPON ÜRETİCİSİ (Agent: `backend-specialist` & `frontend-specialist`)
*   **Aksiyon**: `coupon_builder.py` içerisindeki `format_telegram_coupon` fonksiyonu tamamen LLM'e devredilecek. LLM'den gelen eşsiz ve organik yanıt doğrudan Streamlit arayüzüne basılacak.
*   **Veto Mekanizması**: Eğer Seviye 2 seçilirse, model XGBoost'tan gelen maçları `llm_client`'a gönderip "Onayla/Reddet ve Yorumla" şeklinde çift aşamalı değerlendirmeye sokacak.

### 3. SİSTEM TESTİ VE GÜVENLİK (Agent: `security-auditor` & `test-engineer`)
*   **Aksiyon**: LLM'e giden prompt'ların sızmaması, API anahtarlarının gizli `.env` dosyalarında tutulması için `security_scan.py` ve entegrasyon testleri çalıştırılacak.

---

## 🔴 ORCHESTRATOR EXIT GATE
Bu doküman `/orchestrate` komutu protokolünün *PHASE 1 (Planning)* aşamasıdır.

Onaylıyor musunuz? (Y/N)
- **Y**: Hangi API'yi (Gemini/OpenAI) seçtiğini ve Veto hakkını belirlediğinde Implementation (Uygulama) fazına geçecektir.
- **N**: Planı düzeltirim.
