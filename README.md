# Vize Randevu Takipçisi (Yunanistan & Macaristan)

Türkiye'den **Yunanistan** veya **Macaristan** vize randevusu açıldığında size
**e-posta**, **telefon bildirimi (ntfy)** ve/veya **Telegram** mesajı gönderir.

Sistem GitHub Actions üzerinde **her 10 dakikada bir** kendiliğinden çalışır.
Bilgisayarınızın açık olması gerekmez ve ücretsizdir (repo herkese açık olduğu için
Actions dakikaları sınırsız).

## Nasıl çalışır?

1. `checker.py` randevu durumlarını yayınlayan servislerden (`config.toml` → `[[api]]`)
   Türkiye çıkışlı randevuları çeker.
2. Yunanistan/Macaristan için bir randevu **kapalıdan açığa** geçtiyse (veya bekleme
   listesi açıldıysa) bildirim gönderir. Açık kalmaya devam ederse 6 saatte bir hatırlatır.
3. Hangi randevular için daha önce haber verildiği saklanır, böylece aynı şey için
   her 10 dakikada bir mesaj almazsınız.
4. Kaynakların hepsi ~3 saat boyunca veri vermezse "**sistem veri alamıyor**" diye
   **bir kez** uyarır, düzelince "tekrar çalışıyor" der. Yani sistem sessizce bozulmaz.

## Kurulum (yaklaşık 5 dakika)

Aşağıdaki bildirim yöntemlerinden **en az birini** kurun. Birden fazlasını kurarsanız
hepsine birden gönderilir. Bilgileri GitHub'da şuraya gireceksiniz:

> Repo sayfası → **Settings** → **Secrets and variables** → **Actions** →
> **New repository secret**

### Seçenek A — Telefona anlık bildirim: ntfy (en kolayı, önerilen)

1. Telefonunuza **ntfy** uygulamasını kurun (App Store / Google Play, ücretsiz).
2. Uygulamada **+** → "Subscribe to topic" → tahmin edilmesi zor bir isim yazın,
   örn. `vize-takip-8k3j2q9x`. (Bu isim şifre gibidir; bilen herkes mesajları görebilir.)
3. GitHub'da secret ekleyin:
   | Name | Value |
   |---|---|
   | `NTFY_TOPIC` | `vize-takip-8k3j2q9x` (sizin seçtiğiniz isim) |

Bildirimler "acil" önceliğiyle gelir, telefon sessizde değilse ses çıkarır.

### Seçenek B — E-posta (Gmail)

Gmail normal şifrenizle çalışmaz, bir **uygulama şifresi** gerekir:

1. Google hesabınızda **2 Adımlı Doğrulama** açık olmalı.
2. <https://myaccount.google.com/apppasswords> adresine gidin, "vize" adıyla bir
   uygulama şifresi oluşturun. 16 haneli bir şifre verecek.
3. GitHub'da secret ekleyin:
   | Name | Value |
   |---|---|
   | `SMTP_USER` | gmail adresiniz |
   | `SMTP_PASSWORD` | 16 haneli uygulama şifresi (boşluksuz) |
   | `MAIL_TO` | (isteğe bağlı) mailin gideceği adres; boşsa `SMTP_USER`'a gider. Virgülle birden fazla yazılabilir. |

Gmail dışı bir sağlayıcı için `SMTP_HOST` ve `SMTP_PORT` (465 veya 587) da ekleyin.

> İpucu: Gmail'de bu maillere "Asla spam'e gönderme" + "Önemli olarak işaretle"
> filtresi eklerseniz kaçırmazsınız.

### Seçenek C — Telegram

1. Telegram'da **@BotFather**'a `/newbot` yazın, bota bir isim verin → size bir
   **token** verecek.
2. Oluşturduğunuz bota bir mesaj atın (örn. "merhaba").
3. Tarayıcıda `https://api.telegram.org/bot<TOKEN>/getUpdates` adresini açın,
   `"chat":{"id": 123456789` kısmındaki sayıyı alın.
4. GitHub'da secret ekleyin:
   | Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği token |
   | `TELEGRAM_CHAT_ID` | chat id (birden fazla kişi için virgülle ayırın) |

### Test edin

Repo → **Actions** → **Vize randevu kontrolü** → **Run workflow** →
"Sadece test bildirimi gönder" kutusunu işaretleyin → **Run workflow**.

Bir dakika içinde test mesajı gelmeli. Gelmezse çalışmanın loglarına bakın;
hangi kanalda ne hata olduğu yazar.

Bundan sonra hiçbir şey yapmanıza gerek yok — sistem her 10 dakikada bir kendiliğinden
kontrol eder. Son kontrollerin sonuçlarını **Actions** sekmesinden görebilirsiniz.

## Ayarlar (`config.toml`)

GitHub'da dosyayı açıp kalem ikonuyla düzenleyebilirsiniz:

- **Sadece belirli şehirler:** `cities = ["istanbul"]` (boş = tüm şehirler)
- **Sadece belirli vize türü:** `visa_types = ["tourism"]`
- **Hatırlatma sıklığı:** `remind_every_hours = 6` (0 = sadece ilk açılışta)
- **Kontrol sıklığı:** `.github/workflows/check.yml` içindeki `cron` satırı
  (GitHub en fazla 5 dakikada bir izin verir).

## ⚠️ Önemli: Bilmeniz gerekenler

**Veri kaynakları resmi değildir.** Randevu sistemleri (VFS Global, Kosmos, AS Visa)
giriş + CAPTCHA istediği için doğrudan kontrol edilemiyor. Bu yüzden sistem,
VFS randevu durumlarını topluluk tarafından yayınlayan ücretsiz servisleri kullanır
(`api.visasbot.com`, `api.schengenvisaappointments.com`). Bu servisler zaman zaman
kapanabilir veya formatını değiştirebilir. Öyle olursa sistem size "veri alamıyor"
uyarısı atar; `config.toml` içine yeni bir kaynak adresi eklemeniz yeterli.

**Türkiye'de başvuru merkezleri (2026):**

| Ülke | Şehirler | Aracı kurum | Bu sistem kapsıyor mu? |
|---|---|---|---|
| Yunanistan | Ankara, İzmir ve bağlı iller | VFS Global | ✅ API kaynaklarıyla |
| Yunanistan | İstanbul, Bursa, Trabzon, Çanakkale | Kosmos Vize | ⚠️ Sadece sayfa izleme ile |
| Macaristan | İstanbul, Ankara | AS Visa | ⚠️ API'de varsa + sayfa izleme |

VFS dışındaki kurumlar (Kosmos, AS Visa) için `config.toml` sonundaki **`[[page]]`**
bölümü var: bir sayfayı izleyip, "uygun randevu bulunmamaktadır" gibi bir yazı
kalktığında ya da sayfa değiştiğinde haber verir. Kullanmak için:

1. Randevu sayfasını tarayıcıda açın; giriş gerektirmeyen ve randevu yokken sabit
   bir uyarı yazan bir sayfa bulun.
2. O sayfanın adresini `url`'e, uyarı yazısını `patterns`'e yazın, `mode = "disappears"`
   ve `enabled = true` yapın.

Randevu bilgisi girişten sonra görünüyorsa sayfa izleme işe yaramaz; bu durumda
tek kaynak API'ler olur.

**Diğer notlar:**

- GitHub zamanlanmış görevleri bazen 5–15 dakika geciktirebilir. Yoğun dönemlerde
  randevular dakikalar içinde dolduğu için bildirim gelince hemen bakın.
- GitHub, 60 gün boyunca hiç commit olmayan repolarda zamanlanmış görevleri
  durdurabilir. İş akışı bunu önlemek için kendini açık tutmaya çalışır; yine de
  bir gün "workflow disabled" maili alırsanız Actions sekmesinden **Enable workflow**
  demeniz yeterli.
- Bu araç yalnızca **haber verir**; sizin yerinize randevu almaz.

## Kendi bilgisayarınızda çalıştırmak (isteğe bağlı)

Python 3.11+ yeterli, ek paket gerekmez:

```bash
export NTFY_TOPIC=vize-takip-8k3j2q9x   # veya SMTP_* / TELEGRAM_* değişkenleri
python checker.py --test-notify        # bildirim testi
python checker.py --loop 300           # 5 dakikada bir sürekli kontrol
```

Testler: `python -m unittest discover -s tests -v`
