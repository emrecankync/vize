# Schengen Vize Randevu Takipçisi (İstanbul)

İstanbul'daki başvuru merkezlerinde herhangi bir **Schengen ülkesi** için vize randevusu
açıldığında **Outlook adresinize e-posta** gönderir (isterseniz ek olarak telefon
bildirimi / Telegram da).

Sistem GitHub Actions üzerinde **her 10 dakikada bir** kendiliğinden çalışır.
Bilgisayarınızın açık olması gerekmez ve ücretsizdir.

## Nasıl çalışır?

1. `checker.py`, VFS Global randevu durumlarını yayınlayan servislerden
   (`config.toml` → `[[api]]`) Türkiye'den yapılan başvuruların randevu durumunu çeker.
2. Türkiye → **Schengen ülkesi** → **İstanbul merkezi** olan bir randevu kapalıdan
   **açığa** geçtiyse (veya bekleme listesi açıldıysa) size mail atar. Hangi ülke,
   hangi merkez, hangi vize türü ve ilk müsait tarih mailde yazar.
3. Randevu açık kalırsa 6 saatte bir hatırlatır; aynı randevu için sürekli mail atmaz.
4. Kaynak bir randevuyu 12 saatten uzun süredir kontrol etmediyse o eski "açık"
   bilgisine güvenmez (boşuna heyecanlanmayın diye).
5. Kaynakların hepsi ~3 saat veri vermezse "**sistem veri alamıyor**" diye **bir kez**
   uyarır, düzelince "tekrar çalışıyor" der. Sistem sessizce bozulmaz.

## Kurulum: Outlook'a e-posta (yaklaşık 5 dakika)

> **Neden Gmail?** Microsoft 2026 itibarıyla Outlook/Hotmail hesaplarından şifreyle
> (SMTP) mail göndermeyi tamamen kapattı. Bu yüzden mail **Gmail hesabınızdan
> gönderilir, Outlook adresinize gelir.** Gmail'i açmanıza gerek yok, sadece
> gönderici olarak kullanılır.

### 1. Gmail uygulama şifresi alın

1. Google hesabınızda **2 Adımlı Doğrulama** açık olmalı
   (<https://myaccount.google.com/signinoptions/two-step-verification>).
2. <https://myaccount.google.com/apppasswords> adresine gidin, "vize" adıyla bir
   uygulama şifresi oluşturun. 16 haneli bir şifre verir (bir kere gösterilir, kopyalayın).

### 2. GitHub'a bilgileri girin

Repo sayfası → **Settings** → **Secrets and variables** → **Actions** →
**New repository secret** ile şu üçünü ekleyin:

| Name | Value |
|---|---|
| `SMTP_USER` | Gmail adresiniz (gönderen) |
| `SMTP_PASSWORD` | 16 haneli uygulama şifresi (boşluksuz) |
| `MAIL_TO` | **Outlook adresiniz** (mailin geleceği yer). Virgülle birden fazla yazılabilir. |

### 3. Test edin

Repo → **Actions** → **Vize randevu kontrolü** → **Run workflow** →
"Sadece test bildirimi gönder" kutusunu işaretleyin → **Run workflow**.

1–2 dakika içinde Outlook'a "✅ Vize takip sistemi test bildirimi" maili gelmeli.

### 4. Outlook'ta kaçırmamak için

- Test maili **Gereksiz (Junk)** klasörüne düştüyse: maili açın → **"Gereksiz değil"**
  deyin. Ayrıca gönderen Gmail adresini **Güvenilir gönderenler** listesine ekleyin
  (Ayarlar → Posta → Gereksiz e-posta → Güvenilir gönderenler).
- **Odaklanmış / Diğer** sekmeleri açıksa ve mail "Diğer"e düştüyse: sağ tık →
  **"Her zaman Odaklanmış'a taşı"**.
- Telefonda **Outlook uygulamasının bildirimleri açık** olsun. Mailler "Yüksek önem"
  işaretiyle gelir.

Bundan sonra yapmanız gereken bir şey yok. Son kontrollerin sonuçlarını **Actions**
sekmesinden görebilirsiniz.

## İsteğe bağlı ek kanallar

Mail ile birlikte kurarsanız hepsine birden gönderilir.

**Telefona anlık bildirim (ntfy):** Telefona **ntfy** uygulamasını kurun, tahmin
edilmesi zor bir konu adına abone olun (örn. `vize-takip-8k3j2q9x`), aynı adı
`NTFY_TOPIC` secret'ı olarak ekleyin.

**Telegram:** @BotFather'dan bot açın → `TELEGRAM_BOT_TOKEN`; bota mesaj atıp
`https://api.telegram.org/bot<TOKEN>/getUpdates` adresindeki `chat.id` →
`TELEGRAM_CHAT_ID`.

## Hangi ülkeler için veri var? (kapsam raporu)

Repo → **Actions** → **Vize randevu kontrolü** → **Run workflow** →
"Sadece kaynak kapsam raporu çıkar" → **Run workflow**. Çalışma bitince sayfasında
her kaynağın hangi ülke/merkez için ne durumda ve **kaç saat önce kontrol edilmiş**
veri verdiği tablo halinde görünür. ✅ işaretli satırlar takip edilenlerdir.

Genel bilgi: bu kaynaklar **VFS Global** randevularını izler. İstanbul'da VFS
üzerinden başvuru alan Schengen ülkeleri takip edilebilir. Başka aracı kurum kullanan
ülkeler (örn. Almanya/İtalya → iDATA, İspanya → BLS, Yunanistan → Kosmos,
Macaristan/Portekiz/Slovenya → AS Visa) için genelde veri yoktur. Bu ülkeler
listede olsa bile veri gelmezse sessizce atlanır; zararı yoktur.

## Ayarlar (`config.toml`)

GitHub'da dosyayı açıp kalem ikonuyla düzenleyebilirsiniz:

- **Sadece bazı ülkeler:** `codes = ["fra", "nld"]` veya `codes = ["Fransa", "Hollanda"]`
- **Bazı ülkeleri çıkar:** `exclude = ["Almanya"]`
- **Şehir:** `cities = ["istanbul"]` (boş = tüm şehirler)
- **Sadece belirli vize türü:** `visa_types = ["tourism"]`
- **Hatırlatma sıklığı:** `remind_every_hours = 6` (0 = sadece ilk açılışta)
- **Kontrol sıklığı:** `.github/workflows/check.yml` içindeki `cron` satırı
  (GitHub en fazla 5 dakikada bir izin verir).

## ⚠️ Bilmeniz gerekenler

- **Veri kaynakları resmi değildir.** Randevu sistemleri giriş + CAPTCHA istediği
  için doğrudan kontrol edilemiyor; sistem, VFS randevu durumlarını yayınlayan
  ücretsiz topluluk servislerini kullanır (`api.visasbot.com`,
  `api.schengenvisaappointments.com`). Bunlar kapanabilir veya format değiştirebilir;
  öyle olursa size "veri alamıyor" maili gelir ve `config.toml`'a yeni kaynak eklenir.
- GitHub zamanlanmış görevleri bazen 5–15 dakika geciktirebilir. Randevular
  dakikalar içinde dolduğu için mail gelince hemen bakın.
- GitHub, 60 gün commit olmayan repolarda zamanlanmış görevleri durdurabilir. İş akışı
  bunu önlemeye çalışır; yine de "workflow disabled" maili gelirse Actions
  sekmesinden **Enable workflow** demeniz yeterli.
- Bu araç yalnızca **haber verir**; sizin yerinize randevu almaz.

## Kendi bilgisayarınızda çalıştırmak (isteğe bağlı)

Python 3.11+ yeterli, ek paket gerekmez:

```bash
export SMTP_USER=...@gmail.com SMTP_PASSWORD=... MAIL_TO=...@outlook.com
python checker.py --test-notify   # bildirim testi
python checker.py --report        # hangi ülkeler için veri var?
python checker.py --loop 300      # 5 dakikada bir sürekli kontrol
```

Testler: `python -m unittest discover -s tests -v`
