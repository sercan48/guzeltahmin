# Güzel Tahmin — Maliyet Analizi

## Özet: Ne Zaman Para Harcamak Zorundasın?

| Aşama | Aylık Maliyet | Tetikleyici |
|-------|--------------|-------------|
| Şu an (Shadow) | **$0** | — |
| İlk müşteri öncesi | **~$5-6/ay** | Hetzner (n8n webhook zorunlu) |
| 0–500 müşteri | **~$5-6/ay** | Değişmez |
| 500–2000 müşteri | **~$30-35/ay** | Supabase Pro devreye girer |
| 2000+ müşteri | **~$60-80/ay** | Sunucu büyütme |

---

## Şu An (Shadow Aşaması) — $0/ay

Mevcut stack tamamen ücretsiz çalışıyor:

| Bileşen | Ücret | Limit |
|---------|-------|-------|
| GitHub Actions | Ücretsiz | Public repo = sınırsız |
| Supabase (yeni hesap) | Ücretsiz | 500MB DB, 2 proje |
| Telegram Bot | Ücretsiz | Sınırsız |
| Python/repo | Ücretsiz | — |

**Para harcama: YOK.**

---

## Aşama 1: İlk Müşteri Öncesi — ~$5-6/ay

Cryptomus'tan gelen anlık ödeme bildirimi (IPN webhook) için
**sürekli çalışan bir endpoint şart.** GitHub Actions bunu yapamaz.

### Zorunlu Harcama

| Bileşen | Maliyet | Açıklama |
|---------|---------|----------|
| Hetzner CX21 | **€4.51/ay** | n8n + Caddy + PostgreSQL |
| Alan adı | **~$10/yıl** (~$0.85/ay) | n8n için SSL gerekli |
| **Toplam** | **~$5.36/ay** | |

> **Alan adı alternatifi (ücretsiz):** Cloudflare Tunnel kullanılırsa
> alan adı satın almaya gerek kalmaz. Cloudflare ücretsiz subdomain
> sağlar ve SSL otomatik gelir. Hetzner CX21 tek başına yeter → **$4.51/ay**

### Hâlâ Ücretsiz Kalanlar

| Bileşen | Neden Ücretsiz |
|---------|---------------|
| Supabase | Free tier 500MB — 500 kullanıcı kolayca karşılar |
| Cryptomus | İşlem başına %1 komisyon, sabit ücret yok |
| Telegram Bot API | Tamamen ücretsiz |
| GitHub Actions | Public repo sınırsız |

### Gelir Projeksiyonu (Kırılım Noktası)

```
Hetzner maliyeti karşılamak için gereken minimum:
  Aylık plan $29 → 1 müşteri = $29 → Maliyet $5.36 → KÂR $23.64
  Haftalık plan $9 → 1 müşteri/hafta = $36/ay → KÂR $30.64

İLK MÜŞTERİDE KÂRLISİN.
```

---

## Aşama 2: Büyüme (0–500 Müşteri) — ~$5-6/ay

Bu aralıkta **hiçbir ek maliyet yok.** Mevcut stack taşır:

- Supabase free tier 500MB → 500 kullanıcı ~5MB veri kullanır
- Hetzner CX21 → n8n 500 webhook/gün'ü rahatça işler
- Cryptomus komisyonu gelirden düşülür (sabit maliyet değil)

### 500 Müşteri Senaryo

```
Gelir  : 400 aylık × $29 + 100 haftalık × $9 × 4 = $11,600 + $3,600 = $15,200/ay
Maliyet: $5.36/ay sabit + Cryptomus %1 = $5.36 + $152 = ~$157/ay
Kâr    : ~$15,043/ay
Kâr marjı: %98.9
```

---

## Aşama 3: Ölçekleme (500–2000 Müşteri) — ~$30-35/ay

Bu noktada Supabase free tier sınırına yaklaşılır.

| Bileşen | Eski | Yeni | Fark |
|---------|------|------|------|
| Supabase | Ücretsiz | **$25/ay** (Pro) | +$25 |
| Hetzner | CX21 €4.51 | CX31 €7.49 (gerekirse) | +€3 |
| **Toplam** | $5.36 | ~$33/ay | +$27.64 |

**Supabase Pro tetikleyicileri:**
- DB 500MB dolunca (yaklaşık 5,000-10,000 aktif kullanıcı)
- Proje "paused" edilmemesi için (free tier 1 hafta inactive'de pause eder)
- Daha yüksek connection limiti gerekince

> **Not:** Supabase free tier projeyi 1 hafta aktif kullanım olmadığında
> pause eder. İlk müşteriden itibaren bu risk ortadan kalkar ama
> dikkat edilmeli.

---

## Aşama 4: 2000+ Müşteri — ~$60-80/ay

| Bileşen | Maliyet |
|---------|---------|
| Hetzner CX41 (4 vCPU, 16GB) | €14.29/ay |
| Supabase Pro | $25/ay |
| Yedek/monitoring | ~$10/ay |
| **Toplam** | ~$52/ay |

---

## Veri API Maliyetleri

### football-data.org

Kontrol edildi — **ücretsiz tier WC + tüm büyük ligleri kapsıyor:**

| Lig | Ücretsiz mi? |
|-----|-------------|
| FIFA World Cup | ✅ Ücretsiz |
| Premier League | ✅ Ücretsiz |
| La Liga | ✅ Ücretsiz |
| Bundesliga | ✅ Ücretsiz |
| Serie A | ✅ Ücretsiz |
| Ligue 1 | ✅ Ücretsiz |
| Eredivisie | ✅ Ücretsiz |

**Rate limit:** 10 istek/dakika — günlük bülten için fazlasıyla yeterli.

**Ne zaman ücretli olur?** Dakikada 10'dan fazla istek gerekirse
(çok sayıda lig × birden fazla sorgu). Bu noktada ~€30/ay.
Ama mevcut mimari için **öngörülemeyen bir gelecek.**

### The Odds API (the-odds-api.com)

| Plan | Ücret | Kota |
|------|-------|------|
| Free | **$0** | 500 istek/ay |
| Starter | $79/ay | 30.000 istek/ay |

Günlük 1 bülten = 1 istek/gün = **30 istek/ay** → free tier'ın %6'sı.
500 müşteriye kadar ücretsiz kalır.

**Ne zaman ücretli olur?** Aynı bot'u birden fazla lig/ülke için
çok sık sorgulamaya başlarsan. Şu an için **$0.**

---

## Gizli / Göz Ardı Edilen Maliyetler

| Kalem | Maliyet | Ne Zaman |
|-------|---------|----------|
| Cryptomus komisyonu | **%1** işlem başına | Her ödemede |
| football-data.org ücretli | €30/ay | 10+ istek/dk gerekirse |
| The Odds API ücretli | $79/ay | 500+ istek/ay gerekirse |
| US LLC devlet ücreti (yıllık) | $50–300/yıl | Kurulan eyalete göre |
| Muhasebe/vergi | $200–500/yıl | Gelir başlayınca |

---

## Karar Akışı

```
Şu an mısın?
└─ Evet → $0 harca, shadow devam et

İlk müşteriyi almaya hazır mısın?
└─ Evet → Hetzner CX21 aç ($4.51/ay) + Cloudflare Tunnel (ücretsiz)
           Toplam: $4.51/ay

500 müşteriyi geçtin mi?
└─ Evet → Supabase Pro'ya geç ($25/ay)
           Toplam: ~$30/ay

2000+ müşteriyi geçtin mi?
└─ Evet → Hetzner büyüt (CX31/CX41)
           Toplam: ~$60/ay
```

---

## Sonuç

**Şu an harcaman gereken: $0.**

**İlk müşteri öncesi harcaman gereken: $4.51/ay (Hetzner CX21).**

Bu tek maliyet ilk günden itibaren ilk müşteriyle karşılanıyor.
Supabase, Telegram, GitHub Actions, Cryptomus — hepsi ya ücretsiz
ya da satışa bağlı komisyon modeli.
