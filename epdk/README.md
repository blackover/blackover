# EPDK Petrol Stok İzleme — Masaüstü Uygulaması

EPDK **Petrol Piyasası Stok İzleme Sistemi** web servisine bağlanan, kolay
kullanımlı bir masaüstü uygulaması. **DEP-1**, **DEP-2** ve **DR** tablolarına
kayıt **gönderir**, **günceller**, **siler** ve gönderdiklerinizi **listeler**.

*A desktop client for Turkey's EPDK petroleum stock reporting web service.
Turkish is the primary interface language; an English toggle is built in.*

---

## Neden bu uygulama?

Postman ile çalışırken her istek için token kopyalamak, JSON gövdesini elle
yazmak ve hataları servis reddettikten sonra öğrenmek gerekir. Bu uygulama:

- **Token'ı sizin yerinize yönetir.** 60 dakikalık oturum süresi ekranda geri
  sayar, süre dolmadan otomatik yenilenir.
- **Kayıtları göndermeden önce kontrol eder.** EPDK'nın kuralları (yarım saat
  kuralı, 3 ondalık basamak sınırı, yoğunluk aralığı, büyük harf unvan, 0/1
  gümrük durumu…) yerel olarak uygulanır; hatalı kayıt servise hiç gitmez.
- **Tank ve GTİP listelerini otomatik yükler**, form alanlarında seçilebilir
  hâle getirir.
- **Toplu gönderim** yapar: CSV'den yüzlerce satırı tek seferde gönderir,
  hangi satırın neden reddedildiğini satır satır gösterir.
- **Her işlemi kaydeder.** Ne gönderdiniz, servis ne yanıtladı — hepsi yerel
  bir kayıt defterinde saklanır.
- **Panelde grafiklerle özetler:** tank doluluk oranları, son 24 saatin stok
  seyri ve petrol türü bazında günlük depolama miktarları.
- **Birden fazla müşteriyle çalışır:** kayıtlı müşteriler arasında hızlıca
  geçiş yapar, geçişte tüm listeleri yeni lisansa göre tazeler.
- **Yarım saatlik döngüyü takip eder:** açık saat dilimini, sıradaki dilime
  kalan süreyi ve o dilim için henüz bildirilmemiş tankları gösterir.

---

## Kurulum

Yalnızca **Python 3.9 veya üstü** gerekir. Kurulacak ek paket **yoktur** —
uygulama tamamen standart kütüphane ile yazılmıştır.

```bash
python -m epdk
```

Tarayıcınız `http://127.0.0.1:8787/` adresinde kendiliğinden açılır.

| Ortam | Başlatma |
|---|---|
| Windows | `epdk\BASLAT.bat` dosyasına çift tıklayın |
| macOS / Linux | `./epdk/baslat.sh` |
| Her ortam | `python -m epdk` |

Seçenekler:

```bash
python -m epdk --port 8888      # farklı port
python -m epdk --no-browser     # tarayıcıyı açma
python -m epdk --verbose        # istek günlüğünü göster
```

---

## Kullanım

### 1. Giriş

Giriş ekranında ortamı seçin ve EPDK'nın size verdiği bilgilerle giriş yapın:

| Ortam | Adres |
|---|---|
| **Gerçek Ortam** | `https://petrolstok.epdk.gov.tr/petrolstok/api` |
| **Test Ortamı** | `https://petrolstok-test.epdk.gov.tr/petrolstok/api` |
| **Özel** | kendi girdiğiniz adres (deneme sunucusu için) |

Kullanıcı adı biçimi: `WSU-` + lisans numaranız — örn. `WSU-DEP/444-2/01592`.

> **Parolanız hiçbir zaman diske yazılmaz.** "Oturumu otomatik yenile" seçili
> ise yalnızca uygulama çalıştığı sürece bellekte tutulur; uygulamayı
> kapattığınızda silinir. Diske yalnızca kullanıcı adı, ortam ve görünüm
> tercihleri kaydedilir.

### 2. Birden fazla müşteri

Uygulama birden çok lisans (müşteri) için sırayla kullanılabilir. Başarılı her
girişten sonra müşteri **Kayıtlı müşteriler** listesine eklenir; sonraki
seferde müşteriye tıklayıp yalnızca parolayı yazmanız yeterlidir. Listeden
çıkarmak için satırdaki **×** düğmesini kullanın.

> Listede yalnızca kullanıcı adı, ortam ve (özel ortamsa) adres tutulur.
> **Parola hiçbir müşteri için diske yazılmaz.**

Müşteri değiştirdiğinizde tank listesi, petrol türleri, tablo içerikleri ve
panel grafikleri yeni lisansa göre baştan yüklenir; önceki müşterinin verisi
ekranda kalmaz.

### 3. Sol menü

| Menü | İşlev |
|---|---|
| **Panel** | Açık kayıt sayıları, **bildirim döngüsü**, **grafikler**, son işlemler |
| **Tablo DEP-1** | Tank bazında saatlik stok bildirimi |
| **Tablo DEP-2** | Verilen depolama hizmetleri + "Alınan Hizmetler" sorgusu |
| **Tablo DR** | Alınan depolama hizmetleri + "Verilen Hizmetler" sorgusu |
| **Lisanslı Tanklar** | Lisansınıza kayıtlı tanklar ve kapasiteleri |
| **Petrol Türleri** | Sistemde geçerli GTİP listesi (aranabilir) |
| **İşlem Geçmişi** | Yapılan tüm servis çağrıları, istek/yanıt ayrıntısıyla |
| **Ayarlar** | Dil, tema, zaman aşımı, onay tercihleri |

### 4. Bildirim döngüsü (DEP-1)

DEP-1 verisi yalnızca tam saat (**:00**) ve buçuklarda (**:30**) kabul edilir —
16:00, 16:30, 17:00 … Uygulama bu döngüyü hem panelde hem DEP-1 sayfasının
üstünde bir şeritte gösterir:

- **Açık saat dilimi** — şu an hangi dilime kayıt gönderiliyor (ör. `14:00`).
- **Sıradaki** — bir sonraki dilime kalan süre, saniye saniye geri sayar.
  Dilim değiştiğinde liste ve tank durumu kendiliğinden tazelenir.
- **Tank durumu** — lisansa kayıtlı tankların kaçı bu dilim için bildirildi.
  Yeşil ✓ olanlar gönderilmiş, kesikli çerçeveli olanlar eksik demektir.
  **Eksik tanka tıklayınca form o tank ve o saat dilimiyle açılır.**

> Sağ üstteki **Oturum** rozeti bununla ilgili değildir; o, EPDK oturum
> anahtarının (token) 60 dakikalık ömründen kalan süreyi gösterir.

### 5. Panel grafikleri

Panel, bildirimlerinizi üç grafikte özetler. Grafiklerin üzerine gelince
ayrıntılı değerler görünür; açık ve koyu temada ayrı ayrı okunaklıdır.

| Grafik | Ne gösterir? |
|---|---|
| **Tank Doluluk Oranları** | Her tankın son DEP-1 bildirimindeki stoğu, lisanslı kapasitesine göre. Kapasitenin %75'ini geçen tanklar `△`, %90'ı geçenler `▲` işaretiyle ayrıca uyarır. |
| **Son 24 Saat Stok Seyri** | DEP-1 bildirimlerinden tank başına ton cinsinden stok değişimi (en çok stoklu 5 tank). |
| **Petrol Türüne Göre Gün Başı Stok** | Bugünkü DEP-2 (verilen hizmet) ve DR (alınan hizmet) miktarları, petrol türü bazında yan yana. |

Grafik renkleri renk körlüğüne karşı doğrulanmış bir palettendir; her seri
ayrıca gösterge ve doğrudan etiketle adlandırılır, yani bilgi yalnızca renge
bağlı değildir.

### 6. Kayıt işlemleri

Her tablo sayfasında:

- **Yeni Kayıt** — formu doldurun, isterseniz **Ön kontrol** ile önce
  denetletin, sonra **Gönder**. (Kısayol: `N`)
- **Düzenle** (✎) — kaydı değiştirip `update` metoduyla gönderir.
- **Sil** (🗑) — onay sorar, `delete` metoduyla siler.
- **Çoklu seçim** — satırları işaretleyip topluca silin.
- **CSV indir** — görünen kayıtları Excel uyumlu CSV olarak indirir.
- **Toplu Yükle** — CSV'den toplu gönderim (aşağıya bakın).
- **Ara / sırala** — kolon başlıklarına tıklayarak sıralayın, arama kutusuyla
  filtreleyin.
- **Kolonlar** — listede hangi kolonların görüneceğini seçin. Tercihiniz
  tarayıcıda saklanır. Servisin ürettiği *İşlem Zamanı* kolonu, tablonun yatay
  kaydırma olmadan ekrana sığması için varsayılan olarak gizlidir; buradan
  geri getirebilirsiniz (kaydın tüm alanları 👁 **Ayrıntı** penceresinde de yer
  alır).

### 7. Toplu yükleme (CSV)

**Toplu Yükle → Şablon indir** ile doğru başlıkları içeren bir CSV alın,
Excel'de doldurun ve geri yükleyin. Dosyayı sürükleyip bırakabilir ya da
veriyi doğrudan yapıştırabilirsiniz. Örnek (DR):

```csv
tarih;lisansVeyaIMONumarasi;depHizAlinanSirketUnvani;petrolTuruGTIPNo;gumrukDurumu;gunBasiStokTon
2025-11-22;DEP/475-14/10691;PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ;2710.19.44.00.11;1;18.519
2025-11-22;IMO9321483;MADDOX DMCC;2710.19.67.00.31;1;122.182
```

Ayraç olarak `;` veya `,` kullanılabilir; ondalık ayırıcı olarak hem `.` hem
`,` kabul edilir. Gönderim sonunda başarılı/başarısız sayısı ve her hatalı
satırın gerekçesi listelenir.

---

## Uygulanan EPDK kuralları

Kayıtlar gönderilmeden önce aşağıdaki kurallara göre denetlenir. Kırmızı
işaretlenenler gönderimi **engeller**; sarı olanlar **uyarıdır** (ör. mazeret
girişi yapılmışsa geçmiş tarihli kayıt gönderilebilir).

### Tümü için

| Kural | Sonuç |
|---|---|
| GTİP No sistemde tanımlı listede olmalı | hata |
| Ondalıklı alanlarda virgülden sonra en fazla 3 hane | hata |
| Negatif değer gönderilemez | hata |
| Unvanlar BÜYÜK HARF, kısaltmasız | hata |

### DEP-1

| Kural | Sonuç |
|---|---|
| Saat yalnızca `:00` veya `:30` olabilir | hata |
| İleri tarihli veri gönderilemez | hata |
| Kayıt, ait olduğu saatten sonraki 24 saat içinde gönderilmeli | uyarı |
| Tank numarası lisansa kayıtlı tanklar listesinde olmalı | hata |
| Stok (m³) tank kapasitesini aşamaz | hata |
| Ton değeri m³ değerinin 2 katını aşamaz (yoğunluk ≤ 2 t/m³) | hata |
| Tank içi sıcaklık −100 … 200 arası | hata |
| Yoğunluk 100 … 2000 arası; 0 yalnızca tamamen boş tankta | hata |

### DEP-2 ve DR

| Kural | Sonuç |
|---|---|
| Tarih bugün veya dün olmalı | uyarı |
| İleri tarih | hata |
| Gümrük durumu yalnızca `0` veya `1` | hata |
| Gün başı stok 0 olamaz | hata |
| VKN sayısal (10 hane beklenir) | uyarı |

Mükerrer kayıt kuralları (aynı anahtarla ikinci kayıt gönderilemez) her tablo
formunun altında hatırlatılır; bu kontrol servis tarafında yapılır ve hata
mesajı olduğu gibi gösterilir.

---

## Denemek için: sahte servis

Gerçek servise bağlanmadan uygulamayı denemek ya da ekibe eğitim vermek için
kılavuzdaki sözleşmeyi taklit eden bir sahte servis birlikte gelir:

```bash
# 1. terminal
python -m epdk.mock_service --port 9000

# 2. terminal
python -m epdk
```

Giriş ekranında **Özel** ortamını seçip adres olarak
`http://127.0.0.1:9000/petrolstok/api` girin. İki deneme müşterisi vardır
(ikisinin de parolası `deneme`):

| Kullanıcı adı | Tanklar |
|---|---|
| `WSU-DEP/444-2/01592` | T1, T2, T101, 130 |
| `WSU-DEP/7646-3/39543` | T-1010, T-1015, T-1020, T-2016, T-2904 |

İkisi arasında geçiş yaparak müşteri değişiminin doğru çalıştığını
görebilirsiniz. Sahte servis örnek tanklar, GTİP listesi ve hazır
kayıtlarla gelir ve gerçek servisin hata mesajlarını üretir.

---

## Testler

```bash
python tests/test_epdk_app.py
```

45 test: istemci sözleşmesi (token'ın `message` içinde gelmesi, gövdeli GET
sorguları), doğrulama kuralları, uçtan uca CRUD, toplu yükleme, kayıt defteri,
sunucu güvenliği ve müşteri değişiminde listelerin tazelenmesi.

Sonuncusu gerçek bir tarayıcıda çalışır (`tests/browser_customer_switch.js`);
`node` ve `playwright` kurulu değilse o test atlanır, diğerleri çalışır.

---

## Nasıl çalışır?

```
  Tarayıcı  ⇄  Yerel sunucu (127.0.0.1:8787)  ⇄  EPDK web servisi
   arayüz         token + doğrulama + kayıt defteri
```

Tarayıcı doğrudan EPDK'ya istek atamaz (CORS engeli ve token'ın tarayıcıda
tutulmasının sakıncası). Bu yüzden uygulama bilgisayarınızda küçük bir sunucu
çalıştırır. Sunucu yalnızca `127.0.0.1` adresini dinler ve `/api` uçları özel
bir başlık ister; böylece başka bir web sitesi tarayıcınız üzerinden bu
uygulamaya istek gönderemez.

| Dosya | Görevi |
|---|---|
| `epdk/client.py` | EPDK web servis istemcisi (login, sorgu, save/update/delete) |
| `epdk/schema.py` | Tablo ve alan tanımları — formların **tek kaynağı** |
| `epdk/validation.py` | Gönderim öncesi kural denetimi |
| `epdk/session.py` | Token yönetimi ve otomatik yenileme |
| `epdk/server.py` | Yerel HTTP sunucusu ve `/api` uçları |
| `epdk/store.py` | SQLite kayıt defteri (parolalar maskelenir) |
| `epdk/mock_service.py` | Deneme/eğitim için sahte EPDK servisi |
| `epdk/web/js/slots.js` | Yarım saatlik bildirim döngüsü ve eksik tank takibi |
| `epdk/web/js/charts.js` | Panel grafikleri (bağımlılıksız SVG) |
| `epdk/web/` | Arayüz (bağımlılıksız HTML + CSS + ES modülleri) |

Ayarlar ve kayıt defteri `~/.epdk-stok/` klasöründe tutulur
(`EPDK_APP_HOME` ortam değişkeniyle değiştirilebilir).

---

## Desteklenmeyen tablolar

Kılavuzda ayrıca **TabloK**, **TabloDAT** ve **TabloDATTemin** tabloları
tanımlıdır. Bu sürüm istendiği gibi DEP-1, DEP-2 ve DR tablolarını kapsar.
Diğerleri `epdk/schema.py` içine aynı biçimde bir `TableSpec` eklenerek
arayüze otomatik olarak dâhil edilebilir — form, tablo, CSV şablonu ve
doğrulama şemadan üretilir.

---

## Destek

Servisle ilgili sorunlar (kullanıcı adı, parola, yetki, lisans tanımları) için
EPDK: **petrolstok@epdk.gov.tr** · **0312 201 4774**
