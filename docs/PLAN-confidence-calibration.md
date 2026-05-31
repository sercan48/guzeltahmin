# Güven Kalibrasyonu ve Kendi Kendini İyileştiren Sistem (ML Loop)

## Hedef
Fenerbahçe - Rizespor (2-2) maçı gibi yüksek sürpriz barındıran müsabakalarda Makine Öğrenimi (ML) modelinin verdiği uçuk olasılıkları (%90+), futboldaki şans ve sürpriz faktörlerini hesaba katarak **"Törpülemek"** ve daha gerçekçi bir "Güven Skoru" oluşturmak. Ayrıca geçmiş tahminleri analiz edip cezalandırma katsayılarını otomatik belirleyecek bir "Sürekli Öğrenme" döngüsü kurmak.

---

## Aşama 1: Dinamik Güven Kalibrasyonu (Heuristic Capping)
`src/model/predictor.py` modülünde sadece ham ML çıktılarını değil, filtrelenmiş kesinlik puanlarını döndürecek bir `ConfidenceCalibrator` sistemi inşa edilecek.

- **Limit (Hard Capping)**: Hiçbir futbol maçında ev sahibi güven puanı teknik olarak **%85'i** geçemez. Maksimum tavan limit konulacaktır.
- **Dinamik Ceza Katsayıları (Risk Deductions)**:
  - `is_derby == True`: Güven puanından anında **-%7** düşülecek.
  - `red_card_risk` yüksekliği (hakem / agresiflik): **-%4**.
  - `power_loss` (Sakatlık/Değer eksikliği) veya Form İvme Farkı (Sürpriz potansiyeli): Kritik eksiklerde **-%8'e kadar** dinamik düşme.
- Sonuç olarak, ilk başta %94 çıkan Fenerbahçe maçı, limitler ve risk cezaları uygulandıktan sonra %70-75 bandına çekilerek yanıltıcı bankolara oynanması engellenecek.

## Aşama 2: Kendi Kendini Ayarlama Döngüsü (Feedback Loop)
Sistemin geçmişteki hatalarına bakıp "Fazla özgüvenli davrandığı" durumları tespit etmesi ve ceza katsayılarını otomatik artırması.

- **Analiz Skripti**: Hafta başında çalışacak yeni bir `auto_tuner.py` yazılacak.
- **Mantığı**: `predictions` tablosunda `confidence >= 0.80` olup patlayan (`actual_result != predicted_result`) maçlar sayılacak.
- **Kendi Kendini İyileştirme**: Eğer sistem son haftalarda Top-1 / Banko seçimlerinde yanılıyorsa, "Sistem Otoritesi" algoritması kendi güven formülüne global bir _Overconfidence Bias_ uygulanacak.

## Görev Atamaları
| Görev | İşlem Merkezleri | Hedeflenen Dosyalar |
|---|---|---|
| Kalibrasyon Fonksiyonu | `backend-specialist` | `src/model/predictor.py` |
| Heuristic Ceza Kuralları | `project-planner` | `src/model/calibrator.py` (Yeni) |
| Geribildirim/Öğrenme Döngüsü | `orchestrator` | `src/evaluator/auto_tuner.py` (Yeni) |
| Backtest Entegrasyonu | `backend-specialist` | `src/evaluator/prediction_verifier.py` |

---
## 🏁 Doğrulama Süreci (Phase 4)
- Fikstürden önceki haftanın geçmiş tahminleri alınacak. Eski sistemde %90 güven verilen ama yatmış olan maçlar yeni sistemden (simüle edilerek) geçirilecek.
- Eğer o maçların güncel güven skoru %78'lere kadar inip kuponlardan filtrelenmişse ✅ Başarılı sayılacak.
