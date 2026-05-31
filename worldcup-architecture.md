# Dünya Kupası 2026 Mimari Planı (Lokal Altyapı)

## Goal
Dünya Kupası 2026 tahmin motorunu (İlk 11 kalitesi, Gerçek Elo, İklim/Rakım faktörleri); dış servislere (Supabase/n8n) bağımlı kalmadan **mevcut SQLite veritabanımız ve Python (Telegram JobQueue) altyapımız** üzerine inşa etmek.

## Tasks

- [ ] **Task 1: SQLite Veritabanını Güncellemek**
  - **Aksiyon:** `matches` tablosunu genişletmek ve `venues` ile `match_lineups` (İlk 11'ler) tablolarını oluşturacak `scripts/update_db_wc2026.py` scriptini yazıp çalıştırmak.
  - **Verify:** `sqlite3 data/guzel_tahmin.db ".tables"` komutunun yeni tabloları listelemesi.

- [ ] **Task 2: API'den Canlı İlk 11 Çekme Modülünü Yazmak**
  - **Aksiyon:** Maçtan 45 dakika önce API-Football'dan (`fixtures/lineups`) ilk 11'leri çekip EA FC (FIFA) ratingleri ile eşleştiren fonksiyonu yazmak.
  - **Verify:** Manuel bir ID verilip çalıştırıldığında, JSON formatında ortalama kalite puanını (Örn: 84.5) döndürmesi.

- [ ] **Task 3: Power Score (Dünya Kupası) Algoritmasını Kodlamak**
  - **Aksiyon:** `src/features/world_cup_engine.py` dosyasını oluşturarak Elo (%40), Kadro (%35), Form (%15) ve Çevre Faktörü (%10) denklemlerini yazmak.
  - **Verify:** `python -m src.features.world_cup_engine` çalıştırıldığında test verisi üzerinden iki takımın oranlarını % olarak hatasız vermesi.

- [ ] **Task 4: Telegram JobQueue Entegrasyonunu (n8n Yerine) Kurmak**
  - **Aksiyon:** `app/bot/predictions.py` içerisine, her sabah çalışıp o günün Dünya Kupası maçlarını bulan ve maçtan 45 dk öncesine otomatik "İlk 11 Çek + Tahmini Paylaş" görevi (job) atayan scheduler fonksiyonunu eklemek.
  - **Verify:** Bot çalıştırıldığında log ekranında `Scheduled World Cup lineup fetch for Match X at 17:15` yazısının görünmesi.

## Done When
- [ ] Dış servislere (n8n, Supabase) hiç bulaşılmamış olması.
- [ ] Mevcut bot altyapısının kendi kendine maç saatine göre zamanlayıcı (scheduler) kurabilmesi.
- [ ] SQLite üzerinde EA FC güçlerinin hesaplanıp kaydedilebilmesi.
- [ ] V3 performansını bozmadan turnuvaya özel çalışabilmesi.
