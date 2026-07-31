"""Uçtan uca testler: sahte EPDK servisi + uygulama sunucusu.

Çalıştırma:  python -m unittest discover -s tests   ya da   python tests/test_epdk_app.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Testler kullanıcının gerçek ayar dosyalarına dokunmamalı.
TEMP_HOME = tempfile.mkdtemp(prefix="epdk-test-")
os.environ["EPDK_APP_HOME"] = TEMP_HOME

from epdk import mock_service                      # noqa: E402
from epdk.client import EpdkClient, decode_token   # noqa: E402
from epdk.schema import DEP1, DEP2, DR             # noqa: E402
from epdk.server import create_server              # noqa: E402
from epdk.validation import clean_payload, split_issues, validate  # noqa: E402


def free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def half_hour_ago() -> str:
    now = datetime.now().replace(second=0, microsecond=0)
    now = now.replace(minute=0 if now.minute < 30 else 30)
    return (now - timedelta(minutes=30)).isoformat(timespec="seconds")


class ServersMixin:
    """Sahte servisi ve uygulama sunucusunu ayağa kaldırır."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mock_port = free_port()
        cls.mock = mock_service.create_mock_server("127.0.0.1", cls.mock_port)
        cls.mock_thread = threading.Thread(target=cls.mock.serve_forever, daemon=True)
        cls.mock_thread.start()
        cls.mock_base = f"http://127.0.0.1:{cls.mock_port}/petrolstok/api"

        cls.app_port = free_port()
        cls.app = create_server("127.0.0.1", cls.app_port)
        cls.app_thread = threading.Thread(target=cls.app.serve_forever, daemon=True)
        cls.app_thread.start()
        cls.app_base = f"http://127.0.0.1:{cls.app_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.app.shutdown()
        cls.app.server_close()
        cls.app.state.store.close()

    # ------------------------------------------------------------------
    def call(self, path, method="GET", body=None, headers=None, expect_ok=True):
        url = f"{self.app_base}{path}"
        data = json.dumps(body or {}).encode() if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        # headers={} bilinçli olarak "hiç başlık gönderme" anlamına gelir
        for key, value in ({"X-Epdk-Client": "web"} if headers is None else headers).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode())
                status = response.status
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode())
            status = exc.code
        if expect_ok:
            self.assertTrue(payload.get("ok"), f"{path} → {payload}")
        return payload, status

    def login(self):
        payload, _ = self.call("/api/login", "POST", {
            "username": mock_service.DEMO_USER,
            "password": mock_service.DEMO_PASSWORD,
            "environment": "custom",
            "customBaseUrl": self.mock_base,
        })
        return payload


# ==========================================================================
class TestMockService(ServersMixin, unittest.TestCase):
    """Sahte servis kılavuzdaki sözleşmeye uyuyor mu?"""

    def test_login_returns_token_in_message(self):
        client = EpdkClient(self.mock_base)
        result = client.request("authentication/login", body={
            "username": mock_service.DEMO_USER, "password": mock_service.DEMO_PASSWORD,
        })
        self.assertTrue(result.success)
        self.assertEqual(result.message.count("."), 2, "token JWT biçiminde olmalı")

    def test_login_wrong_password(self):
        client = EpdkClient(self.mock_base)
        result = client.request("authentication/login", body={
            "username": mock_service.DEMO_USER, "password": "yanlis",
        })
        self.assertFalse(result.success)
        self.assertEqual(result.message, "Şifre Hatalı!")

    def test_client_extracts_token_and_claims(self):
        client = EpdkClient(self.mock_base)
        info = client.login(mock_service.DEMO_USER, mock_service.DEMO_PASSWORD)
        self.assertIn("token", info)
        self.assertEqual(info["userName"], mock_service.DEMO_USER)
        claims = decode_token(info["token"])
        self.assertIn("exp", claims)

    def test_query_requires_token(self):
        client = EpdkClient(self.mock_base)
        result = client.query("tablodep1/tablodep1sorgu", mock_service.DEMO_USER, "gecersiz.token.xx")
        self.assertFalse(result.success)
        self.assertIn("Token", result.message)

    def test_get_query_carries_body(self):
        client = EpdkClient(self.mock_base)
        token = client.login(mock_service.DEMO_USER, mock_service.DEMO_PASSWORD)["token"]
        result = client.query("tablodep1/tablodep1sorgu", mock_service.DEMO_USER, token)
        self.assertTrue(result.success)
        self.assertGreater(len(result.data), 0)


# ==========================================================================
class TestValidation(unittest.TestCase):
    """Yerel ön kontrol kuralları."""

    def issues(self, spec, record, **kwargs):
        return validate(spec, record, **kwargs)

    def test_dep1_rejects_quarter_hour(self):
        record = {
            "saat": "2025-11-22T11:15:00", "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10",
            "tankStokTon": "8", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertTrue(any(i["field"] == "saat" for i in errors))

    def test_dep1_accepts_half_hour(self):
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10",
            "tankStokTon": "8", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertEqual(errors, [], errors)

    def test_dep1_four_decimals_rejected(self):
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10.1234",
            "tankStokTon": "8", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertTrue(any(i["field"] == "tankStokM3" for i in errors))

    def test_dep1_ton_cannot_exceed_double_m3(self):
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10",
            "tankStokTon": "25", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertTrue(any(i["field"] == "tankStokTon" for i in errors))

    def test_dep1_density_zero_only_for_empty_tank(self):
        base = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankIciSicaklik": "15",
            "petrolTuruYogunluk": "0",
        }
        loaded = {**base, "tankStokM3": "10", "tankStokTon": "8"}
        errors, _ = split_issues(self.issues(DEP1, loaded))
        self.assertTrue(any(i["field"] == "petrolTuruYogunluk" for i in errors))

        empty = {**base, "tankStokM3": "0", "tankStokTon": "0"}
        errors, _ = split_issues(self.issues(DEP1, empty))
        self.assertEqual(errors, [], errors)

    def test_dep1_temperature_range(self):
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10",
            "tankStokTon": "8", "tankIciSicaklik": "250", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertTrue(any(i["field"] == "tankIciSicaklik" for i in errors))

    def test_dep1_tank_capacity_checked_against_catalog(self):
        tanks = [{"tankNo": "T1", "kapasiteM3": 100}]
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "500",
            "tankStokTon": "400", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record, tanks=tanks))
        self.assertTrue(any(i["field"] == "tankStokM3" for i in errors))

    def test_dep1_unknown_tank(self):
        tanks = [{"tankNo": "T1", "kapasiteM3": 100}]
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "YOK",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "5",
            "tankStokTon": "4", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record, tanks=tanks))
        self.assertTrue(any(i["field"] == "tankNumarasi" for i in errors))

    def test_dep2_requires_uppercase_title(self):
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/1-1/1",
            "ticariUnvan": "Küçük Harfli Unvan A.Ş.", "vkn": "1234567890",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "12.5",
        }
        errors, _ = split_issues(self.issues(DEP2, record))
        self.assertTrue(any(i["field"] == "ticariUnvan" for i in errors))

    def test_dep2_uppercase_turkish_accepted(self):
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/1-1/1",
            "ticariUnvan": "PETLİNE PETROL ÜRÜNLERİ TİCARET ANONİM ŞİRKETİ",
            "vkn": "1234567890", "petrolTuruGTIPNo": "2710.19.43.00.11",
            "gumrukDurumu": "1", "gunBasiStokTon": "12.5",
        }
        errors, _ = split_issues(self.issues(DEP2, record))
        self.assertEqual(errors, [], errors)

    def test_dep2_zero_stock_rejected(self):
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/1-1/1",
            "ticariUnvan": "BÜYÜK HARFLİ UNVAN ANONİM ŞİRKETİ", "vkn": "1234567890",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "0",
        }
        errors, _ = split_issues(self.issues(DEP2, record))
        self.assertTrue(any(i["field"] == "gunBasiStokTon" for i in errors))

    def test_gumruk_must_be_zero_or_one(self):
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/1-1/1",
            "ticariUnvan": "BÜYÜK HARFLİ UNVAN ANONİM ŞİRKETİ", "vkn": "1234567890",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "2",
            "gunBasiStokTon": "5",
        }
        errors, _ = split_issues(self.issues(DEP2, record))
        self.assertTrue(any(i["field"] == "gumrukDurumu" for i in errors))

    def test_old_date_is_warning_not_error(self):
        record = {
            "tarih": (date.today() - timedelta(days=10)).isoformat(),
            "lisansVeyaIMONumarasi": "DEP/1-1/1",
            "depHizAlinanSirketUnvani": "BÜYÜK HARFLİ UNVAN ANONİM ŞİRKETİ",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "5",
        }
        errors, warnings = split_issues(self.issues(DR, record))
        self.assertEqual(errors, [])
        self.assertTrue(any(i["field"] == "tarih" for i in warnings))

    def test_future_date_is_error(self):
        record = {
            "tarih": (date.today() + timedelta(days=1)).isoformat(),
            "lisansVeyaIMONumarasi": "DEP/1-1/1",
            "depHizAlinanSirketUnvani": "BÜYÜK HARFLİ UNVAN ANONİM ŞİRKETİ",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "5",
        }
        errors, _ = split_issues(self.issues(DR, record))
        self.assertTrue(any(i["field"] == "tarih" for i in errors))

    def test_unknown_gtip_rejected_when_catalog_known(self):
        catalog = [{"gtipNo": "2710.19.43.00.11"}]
        record = {
            "tarih": date.today().isoformat(), "lisansVeyaIMONumarasi": "DEP/1-1/1",
            "depHizAlinanSirketUnvani": "BÜYÜK HARFLİ UNVAN ANONİM ŞİRKETİ",
            "petrolTuruGTIPNo": "9999.99.99.99.99", "gumrukDurumu": "1",
            "gunBasiStokTon": "5",
        }
        errors, _ = split_issues(self.issues(DR, record, gtip_list=catalog))
        self.assertTrue(any(i["field"] == "petrolTuruGTIPNo" for i in errors))

    def test_clean_payload_normalises_types(self):
        record = {
            "tarih": " 2025-01-02 ", "lisansNo": " DEP/1-1/1 ",
            "ticariUnvan": "UNVAN", "vkn": "1234567890",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "12,750",
        }
        body = clean_payload(DEP2, record)
        self.assertEqual(body["gunBasiStokTon"], 12.75)
        self.assertEqual(body["vkn"], 1234567890)
        self.assertEqual(body["gumrukDurumu"], 1)
        self.assertEqual(body["lisansNo"], "DEP/1-1/1")

    def test_comma_decimal_accepted(self):
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "10,5",
            "tankStokTon": "8,25", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        errors, _ = split_issues(self.issues(DEP1, record))
        self.assertEqual(errors, [], errors)
        self.assertEqual(clean_payload(DEP1, record)["tankStokM3"], 10.5)


# ==========================================================================
class TestApi(ServersMixin, unittest.TestCase):
    """Uygulama sunucusunun /api uçları."""

    def setUp(self):
        self.login()

    def tearDown(self):
        self.call("/api/logout", "POST", {})

    def test_csrf_header_required(self):
        payload, status = self.call("/api/meta", headers={}, expect_ok=False)
        self.assertEqual(status, 403)
        self.assertIn("Geçersiz", payload["error"])

    def test_meta_exposes_schema(self):
        payload, _ = self.call("/api/meta")
        keys = {table["key"] for table in payload["schema"]["tables"]}
        self.assertEqual(keys, {"dep1", "dep2", "dr"})

    def test_session_is_active_after_login(self):
        payload, _ = self.call("/api/session")
        self.assertTrue(payload["session"]["active"])
        self.assertEqual(payload["session"]["username"], mock_service.DEMO_USER)
        self.assertGreater(payload["session"]["secondsLeft"], 0)

    def test_login_failure_reports_service_message(self):
        self.call("/api/logout", "POST", {})
        payload, status = self.call("/api/login", "POST", {
            "username": mock_service.DEMO_USER, "password": "yanlis",
            "environment": "custom", "customBaseUrl": self.mock_base,
        }, expect_ok=False)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "Şifre Hatalı!")
        self.login()

    def test_lookups(self):
        tanks, _ = self.call("/api/lookup/tanks")
        self.assertTrue(any(t["tankNo"] == "T1" for t in tanks["data"]))
        gtip, _ = self.call("/api/lookup/gtip")
        self.assertTrue(any(g["gtipNo"] == "2710.19.43.00.11" for g in gtip["data"]))

    def test_dep1_full_lifecycle(self):
        self.call("/api/lookup/tanks")
        self.call("/api/lookup/gtip")

        # Sahte servis T1/T101/T2 için hazır seri üretir; mükerrer kayıt
        # kuralına takılmamak için burada boştaki "130" numaralı tank kullanılır.
        record = {
            "saat": half_hour_ago(), "tankNumarasi": "130",
            "petrolTuruGTIPNo": "2710.12.31.00.00", "tankStokM3": "100.5",
            "tankStokTon": "80.25", "tankIciSicaklik": "17.5",
            "petrolTuruYogunluk": "798.5",
        }
        created, _ = self.call("/api/table/dep1", "POST", {"record": record})
        record_id = created["result"]["message"]
        self.assertTrue(record_id)

        listed, _ = self.call("/api/table/dep1")
        self.assertTrue(any(r["id"] == record_id for r in listed["data"]))

        updated, _ = self.call("/api/table/dep1", "PUT", {
            "record": {**record, "id": record_id, "tankStokM3": "120.75"},
        })
        self.assertTrue(updated["result"]["success"])

        listed, _ = self.call("/api/table/dep1")
        row = next(r for r in listed["data"] if r["id"] == record_id)
        self.assertEqual(float(row["tankStokM3"]), 120.75)

        self.call("/api/table/dep1/delete", "POST", {"id": record_id})
        listed, _ = self.call("/api/table/dep1")
        self.assertFalse(any(r["id"] == record_id for r in listed["data"]))

    def test_dep2_lifecycle(self):
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/9-9/9999",
            "ticariUnvan": "TEST DEPOLAMA ANONİM ŞİRKETİ", "vkn": "1234567890",
            "petrolTuruGTIPNo": "2710.19.43.00.26", "gumrukDurumu": "1",
            "gunBasiStokTon": "42.125",
        }
        created, _ = self.call("/api/table/dep2", "POST", {"record": record})
        record_id = created["result"]["message"]
        self.call("/api/table/dep2", "PUT",
                  {"record": {**record, "id": record_id, "gunBasiStokTon": "43.5"}})
        self.call("/api/table/dep2/delete", "POST", {"id": record_id})

    def test_dr_lifecycle(self):
        record = {
            "tarih": date.today().isoformat(),
            "lisansVeyaIMONumarasi": "DEP/475-14/10691",
            "depHizAlinanSirketUnvani": "TEST NAKLİYAT ANONİM ŞİRKETİ",
            "petrolTuruGTIPNo": "2710.19.44.00.11", "gumrukDurumu": "0",
            "gunBasiStokTon": "7.5",
        }
        created, _ = self.call("/api/table/dr", "POST", {"record": record})
        record_id = created["result"]["message"]
        self.call("/api/table/dr/delete", "POST", {"id": record_id})

    def test_invalid_record_is_blocked_before_sending(self):
        record = {
            "saat": "2025-01-01T11:15:00", "tankNumarasi": "T1",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "tankStokM3": "1.23456",
            "tankStokTon": "1", "tankIciSicaklik": "15", "petrolTuruYogunluk": "800",
        }
        payload, status = self.call("/api/table/dep1", "POST",
                                    {"record": record}, expect_ok=False)
        self.assertEqual(status, 422)
        fields = {issue["field"] for issue in payload["details"]["errors"]}
        self.assertIn("saat", fields)
        self.assertIn("tankStokM3", fields)

    def test_service_error_is_reported(self):
        # Aynı kaydı iki kez göndermek mükerrer kayıt hatası vermeli
        record = {
            "tarih": date.today().isoformat(), "lisansNo": "DEP/5-5/5555",
            "ticariUnvan": "MÜKERRER TEST ANONİM ŞİRKETİ", "vkn": "9876543210",
            "petrolTuruGTIPNo": "2710.19.44.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "3.5",
        }
        created, _ = self.call("/api/table/dep2", "POST", {"record": record})
        payload, status = self.call("/api/table/dep2", "POST",
                                    {"record": record}, expect_ok=False)
        self.assertEqual(status, 400)
        self.assertIn("Mükerrer", payload["error"])
        self.call("/api/table/dep2/delete", "POST",
                  {"id": created["result"]["message"]})

    def test_validate_endpoint(self):
        payload, _ = self.call("/api/table/dep2/validate", "POST", {"record": {
            "tarih": date.today().isoformat(), "lisansNo": "X",
            "ticariUnvan": "küçük harf", "vkn": "abc",
            "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
            "gunBasiStokTon": "1",
        }})
        self.assertFalse(payload["valid"])
        fields = {issue["field"] for issue in payload["errors"]}
        self.assertIn("ticariUnvan", fields)
        self.assertIn("vkn", fields)

    def test_bulk_upload(self):
        rows = [
            {"tarih": date.today().isoformat(), "lisansNo": f"DEP/8-8/{i}",
             "ticariUnvan": "TOPLU YÜKLEME ANONİM ŞİRKETİ", "vkn": "1112223334",
             "petrolTuruGTIPNo": "2710.19.43.00.11", "gumrukDurumu": "1",
             "gunBasiStokTon": f"{i + 1}.5"}
            for i in range(3)
        ]
        rows.append({**rows[0], "gunBasiStokTon": "0"})   # hatalı satır

        payload, _ = self.call("/api/table/dep2/bulk", "POST", {"records": rows})
        self.assertEqual(payload["summary"]["succeeded"], 3)
        self.assertEqual(payload["summary"]["failed"], 1)

        for item in payload["results"]:
            if item["success"]:
                self.call("/api/table/dep2/delete", "POST", {"id": item["id"]})

    def test_services_query(self):
        payload, _ = self.call("/api/table/dep2/services")
        self.assertIsInstance(payload["data"], list)

    def test_dashboard_summary(self):
        payload, _ = self.call("/api/dashboard")
        self.assertIn("dep1", payload["summary"])
        self.assertTrue(payload["summary"]["dep1"]["ok"])

    def test_activity_log_records_calls(self):
        self.call("/api/table/dr")
        payload, _ = self.call("/api/log?limit=50")
        actions = {entry["action"] for entry in payload["entries"]}
        self.assertIn("sorgu", actions)
        self.assertIn("login", actions)

    def test_log_masks_password(self):
        payload, _ = self.call("/api/log?limit=200")
        login_entries = [e for e in payload["entries"] if e["action"] == "login"]
        self.assertTrue(login_entries)
        for entry in login_entries:
            self.assertNotIn(mock_service.DEMO_PASSWORD, json.dumps(entry, default=str))

    def test_requires_login(self):
        self.call("/api/logout", "POST", {})
        payload, status = self.call("/api/table/dep1", expect_ok=False)
        self.assertEqual(status, 401)
        self.login()

    def test_static_index_served(self):
        with urllib.request.urlopen(f"{self.app_base}/", timeout=10) as response:
            body = response.read().decode()
        self.assertIn("EPDK Petrol Stok", body)

    def test_static_path_traversal_blocked(self):
        request = urllib.request.Request(f"{self.app_base}/../../etc/passwd")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
        self.assertNotIn("root:", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
