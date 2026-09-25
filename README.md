# Schengen Vize Randevu Takipçisi (İstanbul)

İstanbul'daki VFS Global başvuru merkezlerinde bir **Schengen ülkesi** için randevu
(veya bekleme listesi) açıldığında size **e-posta** ile haber verir.

Sistem GitHub Actions üzerinde **her 10 dakikada bir** kendiliğinden çalışır.
Bilgisayarınızın açık olması gerekmez, ücretsizdir ve **hiçbir şifre gerektirmez**.

## Mail nasıl geliyor?

Randevu açılınca sistem bu repoda başlığı
**"🔔 Vize randevusu: 🇫🇷 Fransa"** gibi olan bir **issue** açar ve sizi etiketler.
GitHub bunu, "workflow failed" maillerini gönderdiği adrese **e-posta olarak** iletir.
Mail şifresi, uygulama şifresi vb. hiçbir şey gerekmez.

**Mailin Outlook'a gelmesi için** (tek seferlik):

1. GitHub → sağ üstte profil resmi → **Settings** → **Emails** → Outlook adresinizi
   ekleyin ve gelen doğrulama mailine tıklayın.
2. **Settings** → **Notifications** → **Default notifications email** kısmında
   Outlook adresinizi seçin.
3. Aynı sayfada **Participating, @mentions and custom** bölümünde **Email** işaretli
   olsun (varsayılan olarak açıktır).

Test için: Repo → **Actions** → **Vize randevu kontrolü** → **Run workflow** →
"Sadece test bildirimi gönder" → **Run workflow**. Birkaç dakika içinde
"✅ Vize takip sistemi test bildirimi" maili gelmeli. Açılan test issue'sunu
kapatabilirsiniz.

> Outlook'ta mail **Gereksiz**'e düşerse "Gereksiz değil" deyin ve
> `notifications@github.com` adresini güvenilir gönderenlere ekleyin.

## Randevuya nereden bakıyor?

Resmî randevu sistemleri (VFS, iDATA, BLS, Kosmos, AS Visa) giriş + CAPTCHA istediği
veya yurt dışı sunucuları engellediği için doğrudan kontrol edilemiyor. Sistem,
randevuları kendisi takip edip sonuçlarını **herkese açık** yayınlayan servisleri okur:

| Kaynak | Ne veriyor | İstanbul |
|---|---|---|
| [Visa Catcher – İstanbul](https://visacatcher.bot/tr/istanbul) (ana kaynak) | Ülke başına anlık durum (yok / bekleme listesi / müsait) ve son kontrol zamanı | ✅ ~14 Schengen ülkesi: Belçika, Bulgaristan, Çekya, Estonya, Finlandiya, Fransa, Hırvatistan, Hollanda, İsviçre, Letonya, Litvanya, Lüksemburg, Malta, Slovenya |
| [vizetakip.app](https://vizetakip.app/) (yedek) | Son bulunan 50 randevu | Şu an çoğunlukla Ankara/İzmir/Gaziantep; İstanbul kaydı çıkarsa bildirilir |

Almanya, İtalya (iDATA), İspanya (BLS), Yunanistan (Kosmos), Macaristan/Portekiz
(AS Visa) için İstanbul'da ücretsiz ve düzenli veri veren bir kaynak şu an bulunamadı.

## Ne zaman mail gelir?

- Bir ülke **"müsait tarih yok"** → **müsait** veya **bekleme listesi açık** durumuna
  geçince (bir kez).
- Randevu açık kalmaya devam ederse 6 saatte bir hatırlatma (bekleme listesi için
  hatırlatma yok, çünkü haftalarca açık kalabiliyor).
- Kaynakların hepsi ~3 saat veri vermezse **bir kez** "⚠️ veri alamıyor", düzelince
  "✅ tekrar çalışıyor". Yani sistem sessizce bozulmaz.

## Hangi ülkeler için veri var? (kapsam raporu)

Repo → **Actions** → **Vize randevu kontrolü** → **Run workflow** →
"Sadece kaynak kapsam raporu çıkar". Çalışma sayfasında her ülkenin o anki durumu
tablo halinde görünür; ✅ işaretliler takip edilenlerdir.

## Ayarlar (`config.toml`)

GitHub'da dosyayı açıp kalem ikonuyla düzenleyebilirsiniz:

- **Sadece bazı ülkeler:** `codes = ["fra", "nld"]` veya `codes = ["Fransa", "Hollanda"]`
- **Bazı ülkeleri çıkar:** `exclude = ["İsviçre"]`
- **Başka şehir:** `[[visacatcher]]` adresini `/tr/ankara`, `/tr/izmir` vb. yapın ve
  `cities`'i güncelleyin.
- **Bekleme listesi bildirimleri istemiyorsanız:** `notify_statuses = ["open"]`
- **Hatırlatma sıklığı:** `remind_every_hours = 6` (0 = sadece ilk açılışta)

## İsteğe bağlı ek kanallar

GitHub maili yeterli, ama isterseniz ek olarak (repo → Settings → Secrets and
variables → Actions):

- **Telefona anlık bildirim (ntfy):** ntfy uygulamasında tahmin edilmesi zor bir konuya
  abone olun, aynı adı `NTFY_TOPIC` secret'ı olarak ekleyin.
- **Telegram:** `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID`.
- **Kendi SMTP'niz:** `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (Gmail uygulama şifresiyle).
  Not: Microsoft 2026'da Outlook hesaplarından şifreyle mail göndermeyi kapattı.

## ⚠️ Bilmeniz gerekenler

- **Kaynaklar resmî değildir.** Yanlış/eksik bilgi olabilir; mail gelince resmî
  siteden kontrol edip randevuyu oradan alın.
- GitHub zamanlanmış görevleri bazen 5–15 dakika geciktirebilir. Randevular dakikalar
  içinde dolduğu için mail gelince hemen bakın.
- GitHub, 60 gün commit olmayan repolarda zamanlanmış görevleri durdurabilir. İş akışı
  bunu önlemeye çalışır; yine de "workflow disabled" maili gelirse Actions sekmesinden
  **Enable workflow** demeniz yeterli.
- Bu araç yalnızca **haber verir**; sizin yerinize randevu almaz.

## Geliştirme

Python 3.11+ yeterli, ek paket gerekmez.

```bash
python checker.py --report        # kaynakların anlık durumu
python checker.py --loop 300      # kendi bilgisayarınızda 5 dakikada bir kontrol
python -m unittest discover -s tests -v
```

`Kaynak keşfi (elle)` iş akışı yeni kaynak adaylarını GitHub sunucularından test
etmek içindir.
