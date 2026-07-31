"""Sahte EPDK servisi — deneme ve eğitim amaçlıdır.

Gerçek servise bağlanmadan uygulamayı denemek, ekip içi eğitim yapmak ve
otomatik testleri çalıştırmak için kullanılır. Kılavuzdaki yanıt sözleşmesini
(``success`` / ``message`` / ``data``) ve temel doğrulama kurallarını taklit
eder.

Çalıştırmak için::

    python -m epdk.mock_service --port 9000

Ardından uygulamada "Özel" ortamı seçip
``http://127.0.0.1:9000/petrolstok/api`` adresini girmeniz yeterlidir.
Kullanıcı adı: WSU-DEP/444-2/01592 · Parola: deneme
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple

DEMO_USER = "WSU-DEP/444-2/01592"
DEMO_PASSWORD = "deneme"
SECRET = b"mock-epdk-secret"
TOKEN_TTL = 3600  # kılavuz: 60 dakika

TANKS = [
    {"id": 1314, "tesisIlIlce": "KIRIKKALE - BAHŞİLİ", "tankTuru": "Gümrüklü",
     "tankNo": "T1", "yakitTuru": "Motorin", "kapasiteM3": 1200.0},
    {"id": 1315, "tesisIlIlce": "KIRIKKALE - BAHŞİLİ", "tankTuru": "Millileşmiş",
     "tankNo": "T2", "yakitTuru": "Benzin", "kapasiteM3": 850.0},
    {"id": 1316, "tesisIlIlce": "KOCAELİ - KÖRFEZ", "tankTuru": "Gümrüklü",
     "tankNo": "T101", "yakitTuru": "Fuel Oil", "kapasiteM3": 5000.0},
    {"id": 1317, "tesisIlIlce": "KOCAELİ - KÖRFEZ", "tankTuru": "Millileşmiş",
     "tankNo": "130", "yakitTuru": "Motorin", "kapasiteM3": 3200.0},
]

PETROL_TYPES = [
    ("1111.00.00.00.01", "Rafineri Yakıt Gazı"),
    ("2207.20.00.10.09", "Dökme Etil Alkol (Benzin Türlerine Harmanlanan Ürün)"),
    ("2707.10.00.00.00", "Benzol (benzen)"),
    ("2707.20.00.00.00", "Toluol (toluen)"),
    ("2707.30.00.00.00", "Ksilol"),
    ("2707.50.00.00.11", "Solvent nafta (çözücü nafta)"),
    ("2710.12.31.00.00", "Havacılık Benzini"),
    ("2710.19.43.00.11", "Motorin (Kükürt ≤ 10 mg/kg)"),
    ("2710.19.43.00.26", "Motorin (Diğerleri)"),
    ("2710.19.44.00.11", "Motorin Türü (Harmanlanmış)"),
    ("2710.19.62.00.11", "Fuel Oil (Kükürt ≤ %1)"),
    ("2710.19.67.00.31", "Fuel Oil (Yüksek Kükürtlü)"),
    ("2710.19.67.00.49", "Fuel Oil (Diğerleri)"),
    ("3826.00.10.00.11", "Biodizel (Yağ Asidi Mono Alkil Esterleri)"),
]

DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")


# --------------------------------------------------------------------- token
def _sign(payload: Dict[str, Any]) -> str:
    def encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    header = encode(json.dumps({"alg": "HS512"}).encode())
    body = encode(json.dumps(payload, ensure_ascii=False).encode())
    signature = hmac.new(SECRET, f"{header}.{body}".encode(), hashlib.sha512).digest()
    return f"{header}.{body}.{encode(signature)}"


def make_token(username: str) -> str:
    now = int(time.time())
    return _sign({
        "sub": "EPVYS_WEB_SERVICE_AUTHENTICATION_TOKEN",
        "exp": now + TOKEN_TTL,
        "iss": "epvys@epdk.gov.tr",
        "licenceNumber": username.replace("WSU-", ""),
        "authority": "OIR_PETROL_DEPO_STOK_WS",
        "userName": username,
        "iat": now,
    })


def read_token(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- veri deposu
class Store:
    """Bellekte tutulan sahte tablolar."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "tablodep1": [], "tablodep2": [], "tablodr": [],
        }
        self._seed()

    def _seed(self) -> None:
        today = date.today()
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        self.tables["tablodep1"] = [
            {
                "id": str(uuid.uuid4()).upper(),
                "kullanici": DEMO_USER,
                "islemZamani": (now - timedelta(hours=hours - 1)).isoformat(timespec="seconds"),
                "saat": (now - timedelta(hours=hours)).isoformat(timespec="seconds"),
                "tankNumarasi": tank,
                "petrolTuruGTIPNo": gtip,
                "tankStokM3": m3,
                "tankStokTon": round(m3 * density / 1000, 3),
                "tankIciSicaklik": temperature,
                "petrolTuruYogunluk": density,
            }
            for hours, tank, gtip, m3, density, temperature in [
                (1, "T1", "2710.19.43.00.11", 842.5, 835.0, 18.4),
                (2, "T1", "2710.19.43.00.11", 851.0, 835.0, 18.1),
                (1, "T101", "2710.19.67.00.31", 4120.75, 968.1, 42.0),
                (3, "T2", "2710.12.31.00.00", 610.25, 745.5, 21.7),
            ]
        ]
        self.tables["tablodep2"] = [
            {
                "id": str(uuid.uuid4()).upper(),
                "kullanici": DEMO_USER,
                "tarih": (today - timedelta(days=offset)).isoformat(),
                "ticariUnvan": unvan,
                "lisansNo": lisans,
                "vkn": vkn,
                "petrolTuruGTIPNo": gtip,
                "gumrukDurumu": gumruk,
                "islemZamani": datetime.now().isoformat(timespec="seconds"),
                "gunBasiStokTon": ton,
            }
            for offset, unvan, lisans, vkn, gtip, gumruk, ton in [
                (0, "PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ",
                 "DAĞ/471-8/10209", 8580044395, "2710.19.44.00.11", 1, 5705.250),
                (0, "GÜZEL ENERJİ AKARYAKIT ANONİM ŞİRKETİ",
                 "DAĞ/4260-2/32126", 1781259215, "2710.19.43.00.26", 0, 122.182),
                (1, "AKARYAKIT VE GAZ DAĞITIM ANONİM ŞİRKETİ",
                 "DEP/475-14/10691", 6090997208, "3826.00.10.00.11", 1, 8.667),
            ]
        ]
        self.tables["tablodr"] = [
            {
                "id": str(uuid.uuid4()).upper(),
                "kullanici": DEMO_USER,
                "tarih": (today - timedelta(days=offset)).isoformat(),
                "lisansVeyaIMONumarasi": lisans,
                "depHizAlinanSirketUnvani": unvan,
                "petrolTuruGTIPNo": gtip,
                "gumrukDurumu": gumruk,
                "islemZamani": datetime.now().isoformat(timespec="seconds"),
                "gunBasiStokTon": ton,
            }
            for offset, lisans, unvan, gtip, gumruk, ton in [
                (0, "DEP/475-14/10691", "PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ",
                 "2710.19.44.00.11", 1, 18.519),
                (1, "IMO9321483", "MADDOX DMCC", "2710.19.67.00.31", 1, 122.182),
            ]
        ]


STORE = Store()


# ----------------------------------------------------------------- kurallar
def _decimals_ok(value: Any, limit: int = 3) -> bool:
    text = str(value)
    if not DECIMAL_RE.match(text):
        return False
    return len(text.split(".")[1]) <= limit if "." in text else True


def _has_lower(text: str) -> bool:
    return any(ch.islower() for ch in text)


def validate(table: str, body: Dict[str, Any]) -> str:
    """Kılavuzdaki başlıca kuralları uygular; hata varsa mesajı döndürür."""
    gtip_codes = {code for code, _ in PETROL_TYPES}

    if table in ("tablodep1", "tablodep2", "tablodr"):
        gtip = str(body.get("petrolTuruGTIPNo") or body.get("petrolTipiGTIPNo") or "")
        if gtip and gtip not in gtip_codes:
            return "Girilen Petrol Türü Hatalıdır."

    if table == "tablodep1":
        raw = str(body.get("saat") or "")
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            return "Bilinmeyen Bir Hata Oluştu."
        if moment.minute not in (0, 30) or moment.second:
            return "Yarım ve tam saatlerde veri gönderilebilir."
        if moment > datetime.now() or moment < datetime.now() - timedelta(hours=24):
            return "Veri ekleme süreniz dolmuştur."

        tank_no = str(body.get("tankNumarasi") or "")
        tank = next((t for t in TANKS if t["tankNo"] == tank_no), None)
        if tank is None:
            return "Girilen Tank Numarası Hatalı."

        for key in ("tankStokM3", "tankStokTon", "tankIciSicaklik", "petrolTuruYogunluk"):
            if not _decimals_ok(body.get(key)):
                return "Girilen Ondalık Değeri En Fazla Üç Hane Olabilir.."
            if float(body.get(key, 0)) < 0 and key != "tankIciSicaklik":
                return "Girilen Değer Pozitif sayı Olmalıdır."

        m3 = float(body.get("tankStokM3", 0))
        ton = float(body.get("tankStokTon", 0))
        temperature = float(body.get("tankIciSicaklik", 0))
        density = float(body.get("petrolTuruYogunluk", 0))

        if m3 > tank["kapasiteM3"]:
            return "Lisansa kayıtlı tankın kapasiteden fazla ürün gönderilemez (m3)"
        if ton > m3 * 2:
            return "Lisansa kayıtlı tankın kapasitesinden fazla ürün gönderilemez (ton)"
        if temperature > 200 or temperature < -100:
            return ("Girilen Tank İçi  Sıcaklık Değeri 200 Değerinden Büyük  ve  "
                    "-100 Den Küçük Olmamalıdır.")
        if density == 0 and (m3 or ton):
            return "Girilen Yoğunluk Değeri Hatalıdır."
        if density and not 100 <= density <= 2000:
            return "Girilen Yoğunluk Değeri Hatalıdır."
        return ""

    # DEP-2 ve DR ortak kuralları
    raw_date = str(body.get("tarih") or "")
    try:
        value = date.fromisoformat(raw_date)
    except ValueError:
        return "Bilinmeyen Bir Hata Oluştu."
    if not (date.today() - timedelta(days=1) <= value <= date.today()):
        return "Bir tarihe ait tablonun gün sonuna dek gönderilmesi gerekmektedir."

    if str(body.get("gumrukDurumu")) not in ("0", "1"):
        return "Gümrük Durumu alanına 0 ya da 1 değerlerinden birini girmeniz gerekmektedir."

    if not _decimals_ok(body.get("gunBasiStokTon")):
        return "Girilen Ondalık  Değeri En Fazla Üç Hane Olabilir.."
    stock = float(body.get("gunBasiStokTon", 0))
    if stock < 0:
        return "Eksi Değer Girilemez."
    if stock == 0:
        return "Gün Başı Stok değeri 0 girilemez"

    unvan = str(body.get("ticariUnvan") or body.get("depHizAlinanSirketUnvani") or "")
    if _has_lower(unvan):
        return "Unvanda kısaltma yapılmaması ve büyük harflerle yazılması gerekmektedir."

    if table == "tablodep2":
        vkn = str(body.get("vkn") or "")
        if not vkn.isdigit():
            return "Vergi Kimlik Numarası Hatalıdır."
    else:
        if not str(body.get("lisansVeyaIMONumarasi") or "").strip():
            return "Lisans Numarası veya IMO Numarası Geçersizdir."
    return ""


UNIQUE_KEYS = {
    "tablodep1": ("saat", "tankNumarasi", "petrolTuruGTIPNo"),
    "tablodep2": ("tarih", "lisansNo", "vkn", "petrolTuruGTIPNo", "ticariUnvan", "gumrukDurumu"),
    "tablodr": ("tarih", "lisansVeyaIMONumarasi", "petrolTuruGTIPNo",
                "depHizAlinanSirketUnvani", "gumrukDurumu"),
}


def duplicate_exists(table: str, body: Dict[str, Any], skip_id: str = "") -> bool:
    keys = UNIQUE_KEYS[table]
    signature = tuple(str(body.get(key, "")) for key in keys)
    for row in STORE.tables[table]:
        if skip_id and str(row["id"]).upper() == skip_id.upper():
            continue
        if tuple(str(row.get(key, "")) for key in keys) == signature:
            return True
    return False


# -------------------------------------------------------------------- HTTP
class MockHandler(BaseHTTPRequestHandler):
    server_version = "MockEPDK/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    # ------------------------------------------------------------------
    def _dispatch(self) -> None:
        path = self.path.split("?")[0].strip("/")
        # Kılavuzdaki "tablodr /delete" yazım hatasına da tolerans göster
        path = re.sub(r"\s+", "", path)
        if not path.startswith("petrolstok/api/"):
            self._send(404, {"success": False, "message": "Bilinmeyen adres."})
            return
        route = path[len("petrolstok/api/"):]
        body = self._read_json()

        if route == "authentication/login":
            self._login(body)
            return

        claims = self._authenticate()
        if claims is None:
            return

        try:
            self._route(route, body)
        except Exception as exc:  # noqa: BLE001
            self._send(200, {"success": False, "message": f"Bilinmeyen Bir Hata Oluştu. ({exc})"})

    def _route(self, route: str, body: Dict[str, Any]) -> None:
        if route == "petrolturlerisorgu":
            self._send(200, {"success": True, "message": None, "data": [
                {"gtipNo": code, "petrolTuru": name,
                 "basTarih": "2017-01-01T00:00:00", "bitTarih": None}
                for code, name in PETROL_TYPES
            ]})
            return

        if route == "lisansakayitlitanklistesisorgu":
            self._send(200, {"success": True, "message": None, "data": TANKS})
            return

        parts = route.split("/")
        if len(parts) != 2 or parts[0] not in STORE.tables:
            self._send(404, {"success": False, "message": "Bilinmeyen metot."})
            return

        table, method = parts
        if method.endswith("sorgu"):
            with STORE.lock:
                self._send(200, {"success": True, "message": None,
                                 "data": list(STORE.tables[table])})
        elif method.endswith("hiz"):
            with STORE.lock:
                self._send(200, {"success": True, "message": None,
                                 "data": list(STORE.tables[table])[:2]})
        elif method == "save":
            self._save(table, body)
        elif method == "update":
            self._update(table, body)
        elif method == "delete":
            self._delete(table, body)
        else:
            self._send(404, {"success": False, "message": "Bilinmeyen metot."})

    # ------------------------------------------------------------------
    def _login(self, body: Dict[str, Any]) -> None:
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not username.startswith("WSU-"):
            self._send(200, {"success": False,
                             "message": f"Kullanıcı Adı  - {username} Hatalı!"})
            return
        if password != DEMO_PASSWORD:
            self._send(200, {"success": False, "message": "Şifre Hatalı!"})
            return
        # Kılavuz: token, message alanı içinde döner
        self._send(200, {"success": True, "message": make_token(username)})

    def _authenticate(self) -> Dict[str, Any] | None:
        header = self.headers.get("Authorization") or ""
        token = header[7:] if header.lower().startswith("bearer ") else ""
        claims = read_token(token) if token else {}
        if not claims or claims.get("exp", 0) < time.time():
            self._send(200, {"success": False, "message": "Token : Geçerli değil !"})
            return None
        return claims

    def _save(self, table: str, body: Dict[str, Any]) -> None:
        error = validate(table, body)
        if error:
            self._send(200, {"success": False, "message": error})
            return
        if duplicate_exists(table, body):
            self._send(200, {"success": False,
                             "message": "Mükerrer Kayıt Lütfen Kayıt Bilgilerinizi Kontrol Ediniz."})
            return
        record = dict(body)
        record["id"] = str(uuid.uuid4()).upper()
        record["islemZamani"] = datetime.now().isoformat(timespec="seconds")
        with STORE.lock:
            STORE.tables[table].append(record)
        self._send(200, {"success": True, "message": record["id"]})

    def _update(self, table: str, body: Dict[str, Any]) -> None:
        record_id = str(body.get("id") or "")
        with STORE.lock:
            existing = next((r for r in STORE.tables[table]
                             if str(r["id"]).upper() == record_id.upper()), None)
        if existing is None:
            self._send(200, {"success": False, "message": "Girilen ID Değeri Hatalıdır."})
            return
        error = validate(table, body)
        if error:
            self._send(200, {"success": False, "message": error})
            return
        if duplicate_exists(table, body, skip_id=record_id):
            self._send(200, {"success": False,
                             "message": "Mükerrer Kayıt Lütfen Kayıt Bilgilerinizi Kontrol Ediniz."})
            return
        with STORE.lock:
            existing.update(body)
            existing["islemZamani"] = datetime.now().isoformat(timespec="seconds")
        self._send(200, {"success": True, "message": existing["id"]})

    def _delete(self, table: str, body: Dict[str, Any]) -> None:
        record_id = str(body.get("id") or "")
        with STORE.lock:
            before = len(STORE.tables[table])
            STORE.tables[table] = [r for r in STORE.tables[table]
                                   if str(r["id"]).upper() != record_id.upper()]
            removed = before - len(STORE.tables[table])
        if not removed:
            self._send(200, {"success": False, "message": "Girilen ID Değeri Hatalıdır."})
            return
        self._send(200, {"success": True, "message": None})

    # ------------------------------------------------------------------
    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class MockServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    verbose = False


def create_mock_server(host: str = "127.0.0.1", port: int = 9000,
                       verbose: bool = False) -> MockServer:
    server = MockServer((host, port), MockHandler)
    server.verbose = verbose
    return server


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sahte EPDK Petrol Stok servisi")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    server = create_mock_server(args.host, args.port, args.verbose)
    base = f"http://{args.host}:{args.port}/petrolstok/api"
    print(f"\n  Sahte EPDK servisi çalışıyor")
    print(f"  ➜  Adres   : {base}")
    print(f"  ➜  Kullanıcı: {DEMO_USER}")
    print(f"  ➜  Parola  : {DEMO_PASSWORD}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
