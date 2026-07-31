"""EPDK tablo tanımları — tek doğruluk kaynağı.

Buradaki tanımlar hem sunucu tarafı doğrulamada hem de arayüzdeki form ve
tablo kolonlarının otomatik üretilmesinde kullanılır. Alan açıklamaları ve
kurallar EPDK "Petrol Piyasası Stok İzleme Sistemi Web Servis Kullanım
Kılavuzu" dokümanından alınmıştır.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class Field:
    """Bir tablo alanının tanımı."""

    name: str
    label_tr: str
    label_en: str
    kind: str = "text"          # text, upper, decimal, integer, date, datetime, gtip, tank, select
    required: bool = True
    # Liste tablosunda kullanılan kısa başlık (boşsa label kullanılır)
    short_tr: str = ""
    short_en: str = ""
    decimals: Optional[int] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    options: Optional[List[Dict[str, Any]]] = None
    help_tr: str = ""
    help_en: str = ""
    placeholder: str = ""
    width: str = "half"         # form ızgarasında kapladığı alan: half | full
    in_list: bool = True        # liste tablosunda kolon olarak gösterilsin mi
    editable: bool = True


@dataclass
class TableSpec:
    """Bir EPDK tablosunun uçtan uca tanımı."""

    key: str
    path: str                    # /api/<path>/save
    query_path: str              # /api/<path>/<query_path>
    label_tr: str
    label_en: str
    short: str
    desc_tr: str
    desc_en: str
    icon: str
    fields: List[Field]
    unique_tr: str               # mükerrer kayıt anahtarı açıklaması
    period_tr: str               # bildirim periyodu açıklaması
    period_en: str
    extra_query: Optional[Dict[str, str]] = None   # ör. depolama hizmeti sorgusu
    supports_write: bool = True

    def field_map(self) -> Dict[str, Field]:
        return {f.name: f for f in self.fields}


GUMRUK_OPTIONS = [
    {"value": "1", "label_tr": "1 – Gümrüklü (antrepo)", "label_en": "1 – Bonded"},
    {
        "value": "0",
        "label_tr": "0 – Millileşmiş (serbest dolaşımda)",
        "label_en": "0 – Free circulation",
    },
]

_DECIMAL_HELP_TR = "Virgülden sonra en fazla 3 hane."
_DECIMAL_HELP_EN = "At most 3 decimal places."


def _gumruk_field() -> Field:
    return Field(
        name="gumrukDurumu",
        label_tr="Gümrük Durumu",
        label_en="Customs status",
        short_tr="Gümrük",
        short_en="Customs",
        kind="select",
        options=GUMRUK_OPTIONS,
        help_tr="Yalnızca 0 veya 1 değeri kabul edilir.",
        help_en="Only 0 or 1 is accepted.",
    )


def _gun_basi_stok_field() -> Field:
    return Field(
        name="gunBasiStokTon",
        label_tr="Gün Başı Stok (ton)",
        label_en="Opening stock (tonnes)",
        short_tr="Stok (ton)",
        short_en="Stock (t)",
        kind="decimal",
        decimals=3,
        minimum=0,
        help_tr=f"{_DECIMAL_HELP_TR} 0 veya negatif değer gönderilemez.",
        help_en=f"{_DECIMAL_HELP_EN} Zero or negative is rejected.",
        placeholder="18.519",
    )


def _gtip_field() -> Field:
    return Field(
        name="petrolTuruGTIPNo",
        label_tr="Petrol Türü (GTİP No)",
        label_en="Petroleum type (GTIP)",
        short_tr="GTİP / Ürün",
        short_en="GTIP / product",
        kind="gtip",
        help_tr="Sistemde tanımlı petrol türleri listesinden seçilmelidir.",
        help_en="Must be one of the petroleum types defined in the system.",
        placeholder="2710.19.43.00.11",
        width="full",
    )


def _tarih_field() -> Field:
    return Field(
        name="tarih",
        label_tr="Tarih",
        label_en="Date",
        short_tr="Tarih",
        short_en="Date",
        kind="date",
        help_tr="Yalnızca bugüne ve bir önceki güne ait veri gönderilebilir.",
        help_en="Only today's and yesterday's data can be submitted.",
    )


DEP1 = TableSpec(
    key="dep1",
    path="tablodep1",
    query_path="tablodep1sorgu",
    label_tr="Tablo DEP-1",
    label_en="Table DEP-1",
    short="DEP-1",
    desc_tr="Tank bazında saatlik stok bildirimi",
    desc_en="Hourly stock notification per tank",
    icon="tank",
    unique_tr="Saat + Tank Numarası + GTİP No aynı olan ikinci bir kayıt gönderilemez.",
    period_tr="Günlük · Kaydın ait olduğu saatten sonraki 24 saat içinde",
    period_en="Daily · within 24 hours of the record's own timestamp",
    fields=[
        Field(
            name="saat",
            label_tr="Veri Saati",
            label_en="Reading time",
            short_tr="Veri Saati",
            short_en="Reading",
            kind="datetime",
            help_tr="Yalnızca tam saat (:00) ve buçuk (:30) kabul edilir. "
            "En fazla 24 saat öncesine ait veri gönderilebilir.",
            help_en="Only :00 and :30 minutes are accepted; at most 24 hours back.",
            placeholder="2025-11-22T11:00:00",
        ),
        Field(
            name="tankNumarasi",
            label_tr="Tank Numarası",
            label_en="Tank number",
            short_tr="Tank",
            short_en="Tank",
            kind="tank",
            help_tr="Lisansa kayıtlı tanklar listesinde bulunmalıdır.",
            help_en="Must exist in the licensed tank list.",
            placeholder="T1",
        ),
        _gtip_field(),
        Field(
            name="tankStokM3",
            label_tr="Tank Stoğu (m³)",
            label_en="Tank stock (m³)",
            short_tr="Stok (m³)",
            short_en="Stock (m³)",
            kind="decimal",
            decimals=3,
            minimum=0,
            help_tr=f"{_DECIMAL_HELP_TR} Tank kapasitesini aşamaz.",
            help_en=f"{_DECIMAL_HELP_EN} Cannot exceed tank capacity.",
            placeholder="1200.000",
        ),
        Field(
            name="tankStokTon",
            label_tr="Tank Stoğu (ton)",
            label_en="Tank stock (tonnes)",
            short_tr="Stok (ton)",
            short_en="Stock (t)",
            kind="decimal",
            decimals=3,
            minimum=0,
            help_tr=f"{_DECIMAL_HELP_TR} m³ değerinin 2 katından fazla olamaz.",
            help_en=f"{_DECIMAL_HELP_EN} Cannot exceed twice the m³ value.",
            placeholder="1020.000",
        ),
        Field(
            name="tankIciSicaklik",
            label_tr="Tank İçi Sıcaklık (°C)",
            label_en="Tank temperature (°C)",
            short_tr="Sıcaklık (°C)",
            short_en="Temp (°C)",
            kind="decimal",
            decimals=3,
            minimum=-100,
            maximum=200,
            help_tr="-100 ile 200 arasında olmalıdır.",
            help_en="Must be between -100 and 200.",
            placeholder="15.000",
        ),
        Field(
            name="petrolTuruYogunluk",
            label_tr="Yoğunluk (kg/m³)",
            label_en="Density (kg/m³)",
            short_tr="Yoğunluk",
            short_en="Density",
            kind="decimal",
            decimals=3,
            minimum=0,
            maximum=2000,
            help_tr="100 – 2000 arası olmalıdır. Yalnızca tamamen boş tanklar "
            "için 0 gönderilebilir.",
            help_en="Must be 100–2000. Zero only for completely empty tanks.",
            placeholder="850.000",
        ),
    ],
)

DEP2 = TableSpec(
    key="dep2",
    path="tablodep2",
    query_path="tablodep2sorgu",
    label_tr="Tablo DEP-2",
    label_en="Table DEP-2",
    short="DEP-2",
    desc_tr="Verilen depolama hizmetlerinin günlük bildirimi",
    desc_en="Daily notification of storage services provided",
    icon="warehouse",
    unique_tr="Tarih + Lisans No + VKN + GTİP No + Ticari Unvan + Gümrük Durumu "
    "aynı olan ikinci bir kayıt gönderilemez.",
    period_tr="Günlük · Bugün ve bir önceki günün verisi",
    period_en="Daily · today's and yesterday's data",
    extra_query={"path": "tablodep2hiz", "label_tr": "Alınan Depolama Hizmetleri",
                 "label_en": "Storage services received"},
    fields=[
        _tarih_field(),
        Field(
            name="lisansNo",
            label_tr="Lisans No",
            label_en="Licence no",
            short_tr="Lisans No",
            short_en="Licence",
            kind="text",
            help_tr="Depolama hizmetini veren firmanın lisans numarası. "
            "Geçerli bir lisans ya da 0 olmalıdır.",
            help_en="Licence number of the storage provider, or 0.",
            placeholder="DEP/475-14/10691",
        ),
        Field(
            name="ticariUnvan",
            label_tr="Ticari Unvan",
            label_en="Trade name",
            short_tr="Ticari Unvan",
            short_en="Trade name",
            kind="upper",
            width="full",
            help_tr="BÜYÜK HARFLERLE ve kısaltma yapılmadan yazılmalıdır.",
            help_en="Must be UPPERCASE with no abbreviations.",
            placeholder="PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ",
        ),
        Field(
            name="vkn",
            label_tr="Vergi Kimlik No",
            label_en="Tax ID",
            short_tr="VKN",
            short_en="Tax ID",
            kind="integer",
            help_tr="Depolama hizmetini veren firmanın VKN'si.",
            help_en="Tax number of the storage provider.",
            placeholder="1781259215",
        ),
        _gtip_field(),
        _gumruk_field(),
        _gun_basi_stok_field(),
    ],
)

DR = TableSpec(
    key="dr",
    path="tablodr",
    query_path="tablodrsorgu",
    label_tr="Tablo DR",
    label_en="Table DR",
    short="DR",
    desc_tr="Alınan depolama hizmetlerinin günlük bildirimi",
    desc_en="Daily notification of storage services received",
    icon="truck",
    unique_tr="Tarih + Lisans/IMO No + GTİP No + Unvan + Gümrük Durumu aynı olan "
    "ikinci bir kayıt gönderilemez.",
    period_tr="Günlük · Bugün ve bir önceki günün verisi",
    period_en="Daily · today's and yesterday's data",
    extra_query={"path": "tablodrhiz", "label_tr": "Verilen Depolama Hizmetleri",
                 "label_en": "Storage services provided"},
    fields=[
        _tarih_field(),
        Field(
            name="lisansVeyaIMONumarasi",
            label_tr="Lisans / IMO No",
            label_en="Licence / IMO no",
            short_tr="Lisans / IMO",
            short_en="Licence / IMO",
            kind="text",
            help_tr="Depolama hizmeti alınan firmanın lisans veya IMO numarası.",
            help_en="Licence or IMO number of the storage provider used.",
            placeholder="DEP/475-14/10691",
        ),
        Field(
            name="depHizAlinanSirketUnvani",
            label_tr="Hizmet Alınan Şirket Unvanı",
            label_en="Provider trade name",
            short_tr="Şirket Unvanı",
            short_en="Provider",
            kind="upper",
            width="full",
            help_tr="BÜYÜK HARFLERLE ve kısaltma yapılmadan yazılmalıdır.",
            help_en="Must be UPPERCASE with no abbreviations.",
            placeholder="PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ",
        ),
        _gtip_field(),
        _gumruk_field(),
        _gun_basi_stok_field(),
    ],
)

TABLES: Dict[str, TableSpec] = {t.key: t for t in (DEP1, DEP2, DR)}

# Listelerde gösterilen, servis tarafından üretilen salt-okunur kolonlar.
READONLY_COLUMNS = [
    {"name": "id", "label_tr": "Kayıt No (ID)", "label_en": "Record ID"},
    {"name": "kullanici", "label_tr": "Kullanıcı", "label_en": "User"},
    {"name": "islemZamani", "label_tr": "İşlem Zamanı", "label_en": "Processed at"},
]


def get_table(key: str) -> TableSpec:
    try:
        return TABLES[key]
    except KeyError:
        raise KeyError(f"Bilinmeyen tablo: {key}") from None


def describe() -> Dict[str, Any]:
    """Arayüzün form ve tabloları kurabilmesi için JSON'a çevrilebilir tanım."""
    return {
        "tables": [
            {
                **{k: v for k, v in asdict(spec).items() if k != "fields"},
                "fields": [asdict(f) for f in spec.fields],
            }
            for spec in TABLES.values()
        ],
        "readonlyColumns": READONLY_COLUMNS,
        "gumrukOptions": GUMRUK_OPTIONS,
    }
