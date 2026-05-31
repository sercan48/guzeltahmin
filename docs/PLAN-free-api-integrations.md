# API Entegrasyonları ve Veri Zenginleştirme Planı

## Hedef
Güzel Tahmin sisteminin isabet oranını (ve Güven Skorunu) maksimize etmek için, ücretsiz Football API'lerini (örn: API-Football, Football-Data.org vb.) sisteme entegre edip canlı verilerle beslemek.

---

## 🛑 Socratic Gate (Cevaplanması Gereken Sorular)
Planı hayata geçirmeden önce şu soruları netleştirmeliyiz:
1. **Hangi API'leri bağlamayı düşünüyorsun?** (Elinizde hazır API anahtarı / Key var mı?)
2. **Sıcaklık/Kota Sınırı (Rate Limit)**: Ücretsiz API'lerin günlük istek (request) limitleri çok düşüktür (Örn: Günde 100 istek). Bu yüzden verileri anlık değil, veritabanına saatlik kaydeden bir "Cache (Önbellek)" sistemi kurmamızı onaylıyor musun?

---

## 🛠️ Potansiyel API-Patterns Entegrasyonları

Yeni API'ler ile sistemimizi şu dört ana koldan geliştirebiliriz:

### 1. Canlı Sakatlık ve Kadro Verisi (Injuries & Suspensions)
- **Kullanım**: Yıldız oyuncuların sakatlık durumunu maçtan 1 gün önce çekeriz.
- **Etkisi**: Yeni geliştireceğimiz "Güven Skoru" ceza mekanizmasında doğrudan `-15%`'e kadar Power Loss cezası kesmemizi sağlar. Fenerbahçe'nin as forvetleri yoksa banko gözüken maç sistemde riskliye çekilir.

### 2. Canlı xG (Beklenen Gol) İstatistikleri
- **Kullanım**: Takımların oynadığı son maçlardaki xG istatistiklerini çekeriz.
- **Etkisi**: Gerçekleşen gol yerine "Yaratılan Tehlike" analiz edildiği için, TGS (Toplam Gol) ve KG Var bahislerinin doğruluk oranı muazzam artar.

### 3. Hakem ve Kart Verileri
- **Kullanım**: Maça atanan hakemin sezonluk kırmızı/sarı kart ortalamaları.
- **Etkisi**: Kırmızı kart riskini statik bir metrikten çıkarıp, maça özel dinamik risk puanına dönüştürür.

---

## Aşama Planı (Task Breakdown)
1. **Araştırma & Mimari (API Proxy)**: Verilen API'nin Rate Limit kurallarını okumak ve veritabanı önbellekleme (Caching) mimarisini kurmak.
2. **Veri Çekme (Ingestion)**: `src/ingestion` klasörüne örn: `custom_api_client.py` yazmak.
3. **Analiz Entegrasyonu**: Çekilen bu taze "Sakatlık/xG" verilerini, `weekend_analyzer.py` ve Modelin (XGBoost) kullandığı `features` listesine yedirmek.
4. **Test**: Yeni API verilerinin arayüzde (Dashboard) bir rozet veya ek uyarı (Örn: ⚠️ "Ev Sahibinde 3 Eksik") olarak gösterilmesi.

---
## Sonraki Adım
Kullanılacak API'lerin isimlerini ve yukarıdaki soruları yanıtladığınızda "Uygulamaya Geç (`/create` komutu ver veya onayla)" diyebiliriz.
