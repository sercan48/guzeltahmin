# Araştırma Kaynakları

Bu klasör, model geliştirme sürecinde başvurulan akademik makaleler,
teknik raporlar ve referans dokümanlar için ayrılmıştır.

Claude her oturumda bu klasörü tarar ve içeriği model kararlarına yansıtır.

---

## Dosya Ekleme Talimatları

### Yerel bilgisayarından:
```
C:\Users\WIN\Desktop\Güzel Tahmin\guzeltahmin\docs\research\
```
klasörüne PDF veya Markdown dosyasını koy, sonra:
```bash
git add docs/research/
git commit -m "research: makale adı ekle"
git push origin main
```

### Desteklenen formatlar

| Format | Nasıl okunur |
|---|---|
| `.pdf` | Read tool (maks 20 sayfa/istek) |
| `.md` / `.txt` | Read tool (tam metin) |
| URL listesi | `urls.md` dosyasına ekle, Claude WebFetch ile çeker |

---

## Önerilen Makale Kategorileri

### Temel Model
- Poisson regresyon futbol tahmininde (Dixon & Coles 1997 orijinal)
- Bivariate Poisson (Maher 1982)
- Expected Goals (xG) metodolojisi

### Kalibrasyon & Değerlendirme
- Brier score ve proper scoring rules
- ECE (Expected Calibration Error) ölçümü
- Isotonic regression kalibrasyon yöntemleri

### Elo & Derecelendirme
- Club Elo metodolojisi (clubelo.com)
- Pi-rating sistemi
- Elo'nun futbola uyarlanması

### Bahis Piyasası
- Closing Line Value (CLV) teorisi
- Pinnacle odds modeli
- Kelly criterion ve bankroll yönetimi

### Lig Özgü
- Home advantage ölçümü
- Sezon içi form modelleme
- Kadro rotasyonu etkisi

---

## Mevcut Dosyalar

*(Henüz makale eklenmedi — yukarıdaki talimatları takip et)*
