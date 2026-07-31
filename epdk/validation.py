"""Gönderim öncesi yerel doğrulama.

EPDK servisi hatalı kayıtları reddeder; bu modül aynı kuralları kayıt
gönderilmeden **önce** uygulayarak kullanıcıyı gereksiz hata mesajlarından
korur.

İki seviye vardır:

``error``    kesin kural ihlali — gönderim engellenir.
``warning``  büyük olasılıkla hata, ancak istisnası olabilir (ör. mazeret
             girişi yapılmış bir tarih). Kullanıcı görür, isterse yine de
             gönderebilir.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from .schema import Field, TableSpec

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")


class Issue(dict):
    """Tek bir doğrulama bulgusu."""

    def __init__(self, field: str, message_tr: str, message_en: str, level: str = "error"):
        super().__init__(
            field=field, level=level, message_tr=message_tr, message_en=message_en
        )


def _has_lowercase(text: str) -> bool:
    """Metinde küçük harf var mı? (Türkçe 'ı/i/ğ/ş' dahil, locale bağımsız)"""
    return any(unicodedata.category(ch) == "Ll" for ch in text)


def to_decimal(value: Any) -> Optional[Decimal]:
    """Kullanıcı girdisini Decimal'e çevirir; '1.234,567' gibi biçimleri anlar."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return None
    if "," in text and "." in text:
        # 1.234,56 → binlik ayıracı nokta kabul edilir
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def normalize_datetime(value: Any) -> str:
    """'2025-11-22T11:00' → '2025-11-22T11:00:00' biçimine getirir."""
    text = str(value or "").strip().replace(" ", "T")
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", text):
        text += ":00"
    return text


def _check_numeric(field: Field, raw: Any, issues: List[Issue]) -> Optional[Decimal]:
    number = to_decimal(raw)
    if number is None:
        issues.append(
            Issue(field.name, f"{field.label_tr}: sayısal bir değer giriniz.",
                  f"{field.label_en}: enter a numeric value.")
        )
        return None
    if field.kind == "integer" and decimal_places(number) > 0:
        issues.append(
            Issue(field.name, f"{field.label_tr}: tam sayı olmalıdır.",
                  f"{field.label_en}: must be a whole number.")
        )
    if field.decimals is not None and decimal_places(number) > field.decimals:
        issues.append(
            Issue(
                field.name,
                f"{field.label_tr}: virgülden sonra en fazla {field.decimals} hane "
                "olabilir. (EPDK: “Girilen Ondalık Değeri En Fazla Üç Hane Olabilir..”)",
                f"{field.label_en}: at most {field.decimals} decimal places.",
            )
        )
    if field.minimum is not None and number < Decimal(str(field.minimum)):
        issues.append(
            Issue(field.name,
                  f"{field.label_tr}: {field.minimum} değerinden küçük olamaz.",
                  f"{field.label_en}: cannot be lower than {field.minimum}.")
        )
    if field.maximum is not None and number > Decimal(str(field.maximum)):
        issues.append(
            Issue(field.name,
                  f"{field.label_tr}: {field.maximum} değerinden büyük olamaz.",
                  f"{field.label_en}: cannot be greater than {field.maximum}.")
        )
    return number


def _check_common(spec: TableSpec, payload: Dict[str, Any]) -> tuple[List[Issue], Dict[str, Decimal]]:
    issues: List[Issue] = []
    numbers: Dict[str, Decimal] = {}

    for field in spec.fields:
        raw = payload.get(field.name)
        empty = raw is None or (isinstance(raw, str) and not raw.strip())

        if empty:
            if field.required:
                issues.append(
                    Issue(field.name, f"{field.label_tr} zorunludur.",
                          f"{field.label_en} is required.")
                )
            continue

        if field.kind in ("decimal", "integer"):
            number = _check_numeric(field, raw, issues)
            if number is not None:
                numbers[field.name] = number

        elif field.kind == "upper":
            text = str(raw).strip()
            if _has_lowercase(text):
                issues.append(
                    Issue(
                        field.name,
                        f"{field.label_tr}: BÜYÜK HARFLERLE yazılmalı ve kısaltma "
                        "yapılmamalıdır. (EPDK: “Unvanda kısaltma yapılmaması ve "
                        "büyük harflerle yazılması gerekmektedir.”)",
                        f"{field.label_en}: must be written in UPPERCASE without "
                        "abbreviations.",
                    )
                )

        elif field.kind == "select":
            allowed = {str(o["value"]) for o in (field.options or [])}
            if str(raw).strip() not in allowed:
                issues.append(
                    Issue(
                        field.name,
                        f"{field.label_tr}: yalnızca "
                        + " ya da ".join(sorted(allowed))
                        + " değeri gönderilebilir.",
                        f"{field.label_en}: only "
                        + " or ".join(sorted(allowed))
                        + " is accepted.",
                    )
                )

        elif field.kind == "date":
            if not DATE_RE.match(str(raw).strip()):
                issues.append(
                    Issue(field.name,
                          f"{field.label_tr}: tarih YYYY-AA-GG biçiminde olmalıdır.",
                          f"{field.label_en}: date must be in YYYY-MM-DD format.")
                )

        elif field.kind == "datetime":
            if not DATETIME_RE.match(str(raw).strip()):
                issues.append(
                    Issue(field.name,
                          f"{field.label_tr}: zaman YYYY-AA-GGTSS:DD:ss biçiminde olmalıdır.",
                          f"{field.label_en}: must be in YYYY-MM-DDTHH:MM:SS format.")
                )

    return issues, numbers


def _check_daily_date(payload: Dict[str, Any], now: datetime, issues: List[Issue]) -> None:
    """DEP-2 / DR: yalnızca bugün ve dün gönderilebilir."""
    raw = str(payload.get("tarih") or "").strip()
    if not DATE_RE.match(raw):
        return
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return
    today = now.date()
    if value > today:
        issues.append(
            Issue("tarih",
                  "İleri tarihli veri gönderilemez.",
                  "Future-dated records cannot be submitted.")
        )
    elif value < today - timedelta(days=1):
        issues.append(
            Issue(
                "tarih",
                "Yalnızca bugüne ve bir önceki güne ait veri gönderilebilir. "
                "(Mazeret girişi yapılmışsa bu uyarıyı yok sayabilirsiniz.)",
                "Only today's and yesterday's data can normally be submitted.",
                level="warning",
            )
        )


def validate(
    spec: TableSpec,
    payload: Dict[str, Any],
    *,
    tanks: Optional[Iterable[Dict[str, Any]]] = None,
    gtip_list: Optional[Iterable[Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> List[Issue]:
    """Bir kaydı EPDK kurallarına göre denetler."""
    now = now or datetime.now()
    issues, numbers = _check_common(spec, payload)

    # --- GTİP kontrolü (liste yüklenebildiyse) -----------------------------
    gtip = str(payload.get("petrolTuruGTIPNo") or "").strip()
    if gtip and gtip_list:
        known = {str(item.get("gtipNo", "")).strip() for item in gtip_list}
        if known and gtip not in known:
            issues.append(
                Issue(
                    "petrolTuruGTIPNo",
                    "Bu GTİP No sistemde tanımlı petrol türleri listesinde yok. "
                    "(EPDK: “Girilen Petrol Türü Hatalıdır.”)",
                    "This GTIP number is not in the system's petroleum type list.",
                )
            )

    if spec.key == "dep1":
        _validate_dep1(payload, numbers, tanks, now, issues)
    else:
        _check_daily_date(payload, now, issues)
        stok = numbers.get("gunBasiStokTon")
        if stok is not None and stok == 0:
            issues.append(
                Issue("gunBasiStokTon",
                      "Gün başı stok değeri 0 girilemez.",
                      "Opening stock cannot be zero.")
            )
        if spec.key == "dep2":
            vkn = str(payload.get("vkn") or "").strip()
            if vkn and vkn.isdigit() and vkn != "0" and len(vkn) not in (10, 11):
                issues.append(
                    Issue("vkn",
                          "Vergi kimlik numarası genellikle 10 hanelidir; "
                          "girilen değeri kontrol ediniz.",
                          "Tax numbers are normally 10 digits; please check.",
                          level="warning")
                )

    return issues


def _validate_dep1(
    payload: Dict[str, Any],
    numbers: Dict[str, Decimal],
    tanks: Optional[Iterable[Dict[str, Any]]],
    now: datetime,
    issues: List[Issue],
) -> None:
    saat_raw = normalize_datetime(payload.get("saat"))
    if DATETIME_RE.match(saat_raw):
        try:
            moment = datetime.fromisoformat(saat_raw)
        except ValueError:
            moment = None
        if moment is not None:
            if moment.minute not in (0, 30) or moment.second != 0:
                issues.append(
                    Issue(
                        "saat",
                        "Yalnızca tam saat (:00) ve buçuklarda (:30) veri "
                        "gönderilebilir. (EPDK: “Yarım ve tam saatlerde veri "
                        "gönderilebilir.”)",
                        "Only :00 and :30 readings are accepted.",
                    )
                )
            if moment > now:
                issues.append(
                    Issue("saat",
                          "İleri tarihli veri gönderilemez.",
                          "Future-dated records cannot be submitted.")
                )
            elif moment < now - timedelta(hours=24):
                issues.append(
                    Issue(
                        "saat",
                        "Bir kayıt, ait olduğu saatten sonraki 24 saat içinde "
                        "gönderilmelidir. (EPDK: “Veri ekleme süreniz dolmuştur.”)",
                        "A record must be sent within 24 hours of its own timestamp.",
                        level="warning",
                    )
                )

    m3 = numbers.get("tankStokM3")
    ton = numbers.get("tankStokTon")
    density = numbers.get("petrolTuruYogunluk")

    if m3 is not None and ton is not None and ton > m3 * 2:
        issues.append(
            Issue(
                "tankStokTon",
                "Ton değeri, m³ değerinin 2 katından fazla olamaz (yoğunluk en "
                "fazla 2 ton/m³ olabilir).",
                "Tonnes cannot exceed twice the m³ value (max density 2 t/m³).",
            )
        )

    if density is not None:
        empty_tank = (m3 is None or m3 == 0) and (ton is None or ton == 0)
        if density == 0 and not empty_tank:
            issues.append(
                Issue(
                    "petrolTuruYogunluk",
                    "Yoğunluk yalnızca m³ ve ton değerleri 0 olan boş tanklar için "
                    "0 olabilir. (EPDK: “Girilen Yoğunluk Değeri Hatalıdır.”)",
                    "Density may be 0 only when both m³ and tonnes are 0.",
                )
            )
        elif density != 0 and not (Decimal("100") <= density <= Decimal("2000")):
            issues.append(
                Issue("petrolTuruYogunluk",
                      "Yoğunluk 100 ile 2000 arasında olmalıdır.",
                      "Density must be between 100 and 2000.")
            )

    tank_no = str(payload.get("tankNumarasi") or "").strip()
    if tank_no and tanks:
        catalog = {str(t.get("tankNo", "")).strip(): t for t in tanks}
        if catalog and tank_no not in catalog:
            issues.append(
                Issue(
                    "tankNumarasi",
                    "Bu tank numarası lisansa kayıtlı tanklar listesinde yok. "
                    "(EPDK: “Girilen Tank Numarası Hatalı.”)",
                    "This tank number is not in the licensed tank list.",
                )
            )
        elif tank_no in catalog and m3 is not None:
            capacity = to_decimal(catalog[tank_no].get("kapasiteM3"))
            if capacity is not None and capacity > 0 and m3 > capacity:
                issues.append(
                    Issue(
                        "tankStokM3",
                        f"Tank kapasitesi {capacity} m³; bundan fazla ürün "
                        "gönderilemez.",
                        f"Tank capacity is {capacity} m³; stock cannot exceed it.",
                    )
                )


def split_issues(issues: List[Issue]) -> tuple[List[Issue], List[Issue]]:
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    return errors, warnings


def clean_payload(spec: TableSpec, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Servise gönderilecek gövdeyi normalize eder (sayı, tarih, boşluk)."""
    result: Dict[str, Any] = {}
    for field in spec.fields:
        if field.name not in payload:
            continue
        raw = payload[field.name]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if field.kind in ("decimal",):
            number = to_decimal(raw)
            result[field.name] = float(number) if number is not None else raw
        elif field.kind in ("integer", "select"):
            number = to_decimal(raw)
            if number is not None and decimal_places(number) == 0:
                result[field.name] = int(number)
            else:
                result[field.name] = raw
        elif field.kind == "datetime":
            result[field.name] = normalize_datetime(raw)
        else:
            result[field.name] = str(raw).strip()
    return result
