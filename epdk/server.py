"""Yerel HTTP sunucusu.

Tarayıcı doğrudan EPDK servisine istek atamaz (CORS + token güvenliği), bu
yüzden uygulama kendi bilgisayarınızda küçük bir sunucu çalıştırır:

    tarayıcı  ⇄  bu sunucu (127.0.0.1)  ⇄  EPDK web servisi

Sunucu yalnızca yerel arayüzü dinler ve /api uçları özel bir başlık ister;
böylece başka bir web sitesi tarayıcınız üzerinden bu uygulamaya istek
gönderemez.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import threading
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from . import APP_NAME, __version__
from .client import EpdkError
from .config import (
    ENVIRONMENTS,
    db_path,
    load_settings,
    resolve_base_url,
    save_settings,
)
from .schema import TABLES, describe, get_table
from .session import Session
from .store import ActivityStore
from .validation import clean_payload, split_issues, validate

WEB_ROOT = Path(__file__).parent / "web"
CLIENT_HEADER = "X-Epdk-Client"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


class AppState:
    """Sunucunun paylaşılan durumu."""

    def __init__(self) -> None:
        self.settings = load_settings()
        self.session = Session()
        self.store = ActivityStore(db_path(), limit=int(self.settings.get("log_limit", 2000)))
        self.lock = threading.Lock()

    # -------------------------------------------------------------- yardım
    def base_url(self) -> str:
        return resolve_base_url(self.settings)

    def require_session(self) -> Session:
        if not self.session.active:
            raise ApiError("Oturum açık değil. Lütfen giriş yapın.", status=401)
        return self.session

    def call(
        self,
        *,
        action: str,
        table_key: str,
        endpoint: str,
        runner: Callable[[str], Any],
        request_payload: Any = None,
        record_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Servis çağrısını çalıştırır, sonucu kayıt defterine yazar."""
        session = self.require_session()
        try:
            token = session.ensure_token()
            result = runner(token)
        except EpdkError as exc:
            self.store.record(
                action=action,
                success=False,
                environment=session.environment,
                username=session.username,
                table_key=table_key,
                endpoint=endpoint,
                message=exc.message,
                status=exc.status,
                record_id=record_id,
                request=request_payload,
                response=exc.payload or None,
            )
            raise ApiError(exc.message, status=502 if exc.status is None else 400) from exc

        self.store.record(
            action=action,
            success=result.success,
            environment=session.environment,
            username=session.username,
            table_key=table_key,
            endpoint=endpoint,
            status=result.status,
            message=result.message,
            record_id=record_id,
            request=request_payload,
            response=result.raw,
            duration_ms=result.duration_ms,
        )
        return result.as_dict()


# ---------------------------------------------------------------- uç noktalar
class Api:
    """/api altındaki uçların gerçeklemesi."""

    def __init__(self, state: AppState):
        self.state = state

    # ------------------------------------------------------------ oturum
    def meta(self, _payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        settings = dict(self.state.settings)
        return {
            "app": {"name": APP_NAME, "version": __version__},
            "environments": ENVIRONMENTS,
            "settings": settings,
            "schema": describe(),
            "session": self.state.session.describe(),
        }

    def session_info(self, _payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        return {"session": self.state.session.describe()}

    def login(self, payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        environment = str(payload.get("environment") or self.state.settings["environment"])
        custom_url = str(payload.get("customBaseUrl") or "").strip()

        if not username or not password:
            raise ApiError("Kullanıcı adı ve parola zorunludur.")

        settings = self.state.settings
        settings["environment"] = environment
        if environment == "custom":
            if not custom_url:
                raise ApiError("Özel ortam için adres girmelisiniz.")
            settings["custom_base_url"] = custom_url
        base_url = resolve_base_url(settings)
        if not base_url:
            raise ApiError("Servis adresi belirlenemedi.")

        auto_renew = bool(payload.get("autoRenew", settings.get("auto_renew", True)))
        try:
            info = self.state.session.login(
                base_url=base_url,
                environment=environment,
                username=username,
                password=password,
                timeout=int(settings.get("timeout", 45)),
                verify_ssl=True,
                auto_renew=auto_renew,
            )
        except EpdkError as exc:
            self.state.store.record(
                action="login",
                success=False,
                environment=environment,
                username=username,
                endpoint="authentication/login",
                message=exc.message,
                status=exc.status,
                request={"username": username, "password": "••••••"},
            )
            raise ApiError(exc.message, status=401) from exc

        self.state.store.record(
            action="login",
            success=True,
            environment=environment,
            username=username,
            endpoint="authentication/login",
            message="Oturum açıldı",
            request={"username": username, "password": "••••••"},
        )

        # Kullanıcı adı ve ortam bir sonraki açılış için saklanır (parola değil).
        settings["username"] = username
        settings["auto_renew"] = auto_renew
        self.state.settings = save_settings(settings)
        return {"session": info}

    def logout(self, _payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        self.state.session.logout()
        return {"session": self.state.session.describe()}

    # ----------------------------------------------------------- ayarlar
    def update_settings(self, payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        self.state.settings = save_settings(payload or {})
        self.state.store.limit = int(self.state.settings.get("log_limit", 2000))
        return {"settings": self.state.settings}

    # -------------------------------------------------------- referanslar
    def tanks(self, _payload: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
        return self._lookup(
            cache_key="tanks",
            endpoint="lisansakayitlitanklistesisorgu",
            action="tank-listesi",
            refresh=_flag(query, "refresh"),
        )

    def gtip(self, _payload: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
        return self._lookup(
            cache_key="gtip",
            endpoint="petrolturlerisorgu",
            action="petrol-turleri",
            refresh=_flag(query, "refresh"),
        )

    def _lookup(self, *, cache_key: str, endpoint: str, action: str, refresh: bool) -> Dict[str, Any]:
        session = self.state.require_session()
        if not refresh:
            cached = session.cached(cache_key)
            if cached is not None:
                return {"data": cached, "cached": True}

        result = self.state.call(
            action=action,
            table_key="",
            endpoint=endpoint,
            runner=lambda token: session.client().query(endpoint, session.username, token),
            request_payload={"kullanici": session.username},
        )
        if not result["success"]:
            raise ApiError(result.get("message") or "Liste alınamadı.", status=400)
        data = result.get("data") or []
        session.cache(cache_key, data)
        return {"data": data, "cached": False}

    # ------------------------------------------------------------ tablolar
    def list_records(self, table_key: str, _payload: Dict[str, Any],
                     query: Dict[str, Any]) -> Dict[str, Any]:
        spec = _spec(table_key)
        session = self.state.require_session()
        endpoint = f"{spec.path}/{spec.query_path}"
        result = self.state.call(
            action="sorgu",
            table_key=spec.key,
            endpoint=endpoint,
            runner=lambda token: session.client().query(endpoint, session.username, token),
            request_payload={"kullanici": session.username},
        )
        if not result["success"]:
            raise ApiError(result.get("message") or "Kayıtlar alınamadı.", status=400)
        return {"data": result.get("data") or [], "fetchedAt": datetime.now().isoformat()}

    def list_services(self, table_key: str, _payload: Dict[str, Any],
                      _query: Dict[str, Any]) -> Dict[str, Any]:
        spec = _spec(table_key)
        if not spec.extra_query:
            raise ApiError("Bu tablo için hizmet sorgusu tanımlı değil.", status=404)
        session = self.state.require_session()
        endpoint = f"{spec.path}/{spec.extra_query['path']}"
        result = self.state.call(
            action="hizmet-sorgu",
            table_key=spec.key,
            endpoint=endpoint,
            runner=lambda token: session.client().query(endpoint, session.username, token),
            request_payload={"kullanici": session.username},
        )
        if not result["success"]:
            raise ApiError(result.get("message") or "Kayıtlar alınamadı.", status=400)
        return {"data": result.get("data") or []}

    def validate_record(self, table_key: str, payload: Dict[str, Any],
                        _query: Dict[str, Any]) -> Dict[str, Any]:
        spec = _spec(table_key)
        issues = self._validate(spec, payload.get("record") or {})
        errors, warnings = split_issues(issues)
        return {"errors": errors, "warnings": warnings, "valid": not errors}

    def save_record(self, table_key: str, payload: Dict[str, Any],
                    _query: Dict[str, Any]) -> Dict[str, Any]:
        return self._write(table_key, payload, mode="save")

    def update_record(self, table_key: str, payload: Dict[str, Any],
                      _query: Dict[str, Any]) -> Dict[str, Any]:
        return self._write(table_key, payload, mode="update")

    def delete_record(self, table_key: str, payload: Dict[str, Any],
                      _query: Dict[str, Any]) -> Dict[str, Any]:
        spec = _spec(table_key)
        session = self.state.require_session()
        record_id = str(payload.get("id") or "").strip()
        if not record_id:
            raise ApiError("Silinecek kaydın ID değeri gereklidir.")

        result = self.state.call(
            action="silme",
            table_key=spec.key,
            endpoint=f"{spec.path}/delete",
            runner=lambda token: session.client().delete(
                spec.path, record_id, session.username, token
            ),
            request_payload={"id": record_id, "kullanici": session.username},
            record_id=record_id,
        )
        if not result["success"]:
            raise ApiError(result.get("message") or "Kayıt silinemedi.", status=400)
        return {"result": result}

    def bulk_save(self, table_key: str, payload: Dict[str, Any],
                  _query: Dict[str, Any]) -> Dict[str, Any]:
        """Birden fazla kaydı sırayla gönderir; her satırın sonucunu döndürür."""
        spec = _spec(table_key)
        rows = payload.get("records") or []
        if not isinstance(rows, list) or not rows:
            raise ApiError("Gönderilecek kayıt bulunamadı.")
        if len(rows) > 500:
            raise ApiError("Tek seferde en fazla 500 kayıt gönderilebilir.")
        skip_warnings = bool(payload.get("ignoreWarnings", True))

        results: List[Dict[str, Any]] = []
        for index, row in enumerate(rows):
            entry: Dict[str, Any] = {"index": index}
            try:
                outcome = self._write(
                    table_key,
                    {"record": row, "ignoreWarnings": skip_warnings},
                    mode="save",
                )
                entry.update(
                    success=True,
                    id=outcome["result"].get("message"),
                    message=outcome["result"].get("message"),
                )
            except ApiError as exc:
                entry.update(success=False, message=exc.message, details=exc.details)
            results.append(entry)

        succeeded = sum(1 for item in results if item["success"])
        return {
            "results": results,
            "summary": {
                "total": len(results),
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            },
        }

    # ------------------------------------------------------------ yardımcı
    def _validate(self, spec, record: Dict[str, Any]):
        session = self.state.session
        return validate(
            spec,
            record,
            tanks=session.cached("tanks") if spec.key == "dep1" else None,
            gtip_list=session.cached("gtip"),
        )

    def _write(self, table_key: str, payload: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
        spec = _spec(table_key)
        session = self.state.require_session()
        record = payload.get("record") or {}
        ignore_warnings = bool(payload.get("ignoreWarnings", True))

        issues = self._validate(spec, record)
        errors, warnings = split_issues(issues)
        if errors:
            raise ApiError(
                "Kayıt EPDK kurallarına uymuyor; gönderilmedi.",
                status=422,
                details={"errors": errors, "warnings": warnings},
            )
        if warnings and not ignore_warnings:
            raise ApiError(
                "Kayıtta dikkat edilmesi gereken noktalar var.",
                status=422,
                details={"errors": [], "warnings": warnings},
            )

        body = clean_payload(spec, record)
        body["kullanici"] = session.username

        record_id = str(record.get("id") or "").strip()
        if mode == "update":
            if not record_id:
                raise ApiError("Güncelleme için kaydın ID değeri gereklidir.")
            body["id"] = record_id

        endpoint = f"{spec.path}/{mode}"
        runner = (
            (lambda token: session.client().save(spec.path, body, token))
            if mode == "save"
            else (lambda token: session.client().update(spec.path, body, token))
        )
        result = self.state.call(
            action="kayit" if mode == "save" else "guncelleme",
            table_key=spec.key,
            endpoint=endpoint,
            runner=runner,
            request_payload=body,
            record_id=record_id or None,
        )
        if not result["success"]:
            raise ApiError(result.get("message") or "İşlem başarısız.", status=400)
        return {"result": result, "warnings": warnings}

    # ------------------------------------------------------------- defter
    def log(self, _payload: Dict[str, Any], query: Dict[str, Any]) -> Dict[str, Any]:
        entries = self.state.store.list(
            limit=int(_single(query, "limit", "200") or 200),
            table_key=_single(query, "table", ""),
            only_failures=_flag(query, "failures"),
            search=_single(query, "q", ""),
        )
        return {"entries": entries, "stats": self.state.store.stats()}

    def clear_log(self, _payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        self.state.store.clear()
        return {"cleared": True}

    # ------------------------------------------------------------- özet
    def dashboard(self, _payload: Dict[str, Any], _query: Dict[str, Any]) -> Dict[str, Any]:
        session = self.state.session
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        summary: Dict[str, Any] = {}

        if session.active:
            for key, spec in TABLES.items():
                endpoint = f"{spec.path}/{spec.query_path}"
                try:
                    result = self.state.call(
                        action="sorgu",
                        table_key=key,
                        endpoint=endpoint,
                        runner=lambda token, ep=endpoint: session.client().query(
                            ep, session.username, token
                        ),
                        request_payload={"kullanici": session.username},
                    )
                    rows = result.get("data") or [] if result["success"] else []
                    summary[key] = {
                        "ok": bool(result["success"]),
                        "message": result.get("message"),
                        "total": len(rows),
                        "today": _count_for_day(rows, today),
                        "yesterday": _count_for_day(rows, yesterday),
                        "lastRecord": _latest(rows),
                        # Grafikler için ham satırlar (arayüz tarafında işlenir)
                        "rows": rows,
                    }
                except ApiError as exc:
                    summary[key] = {"ok": False, "message": exc.message, "total": 0,
                                    "today": 0, "yesterday": 0, "lastRecord": None,
                                    "rows": []}

        return {
            "summary": summary,
            "tanks": self._tanks_for_charts(),
            "activity": self.state.store.list(limit=8),
            "stats": self.state.store.stats(),
            "session": session.describe(),
        }

    def _tanks_for_charts(self) -> List[Dict[str, Any]]:
        """Tank kapasiteleri — doluluk grafiği için. Hata olursa boş liste."""
        session = self.state.session
        if not session.active:
            return []
        cached = session.cached("tanks")
        if cached is not None:
            return cached
        try:
            return self._lookup(
                cache_key="tanks",
                endpoint="lisansakayitlitanklistesisorgu",
                action="tank-listesi",
                refresh=False,
            )["data"]
        except ApiError:
            return []


def _count_for_day(rows: List[Dict[str, Any]], day) -> int:
    stamp = day.isoformat()
    total = 0
    for row in rows:
        value = str(row.get("tarih") or row.get("saat") or "")
        if value.startswith(stamp):
            total += 1
    return total


def _latest(rows: List[Dict[str, Any]]) -> Optional[str]:
    stamps = [str(r.get("islemZamani") or "") for r in rows if r.get("islemZamani")]
    return max(stamps) if stamps else None


def _spec(table_key: str):
    try:
        return get_table(table_key)
    except KeyError as exc:
        raise ApiError(str(exc), status=404) from exc


def _single(query: Dict[str, List[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    return values[0] if values else default


def _flag(query: Dict[str, List[str]], key: str) -> bool:
    return _single(query, key, "").lower() in {"1", "true", "yes", "evet"}


# --------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"EPDKStok/{__version__}"
    api: Api = None          # type: ignore[assignment]
    state: AppState = None   # type: ignore[assignment]

    # Konsolu kirletmemek için varsayılan erişim günlüğü kapatılır.
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("GET", parsed)
        else:
            self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("POST", parsed)
        else:
            self._send_json({"error": "Bulunamadı"}, status=404)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("PUT", parsed)
        else:
            self._send_json({"error": "Bulunamadı"}, status=404)

    # ------------------------------------------------------------------
    def _handle_api(self, method: str, parsed) -> None:
        # Basit CSRF koruması: tarayıcıdaki başka bir site özel başlık
        # gönderemez (ön-uçuş isteği yerel sunucuda reddedilir).
        if self.headers.get(CLIENT_HEADER) != "web":
            self._send_json({"error": "Geçersiz istek kaynağı."}, status=403)
            return

        query = parse_qs(parsed.query)
        try:
            payload = self._read_json() if method in ("POST", "PUT") else {}
            handler, args = self._route(method, parsed.path)
            result = handler(*args, payload, query)
            self._send_json({"ok": True, **(result or {})})
        except ApiError as exc:
            self._send_json(
                {"ok": False, "error": exc.message, "details": exc.details},
                status=exc.status,
            )
        except Exception as exc:  # noqa: BLE001 - beklenmeyen hatayı da bildir
            traceback.print_exc()
            self._send_json(
                {"ok": False, "error": f"Beklenmeyen hata: {exc}"}, status=500
            )

    def _route(self, method: str, path: str) -> Tuple[Callable, Tuple[Any, ...]]:
        api = self.api
        parts = [unquote(p) for p in path.strip("/").split("/")][1:]  # 'api' atılır

        if not parts:
            raise ApiError("Bilinmeyen uç.", status=404)

        head, rest = parts[0], parts[1:]

        if head == "meta" and method == "GET":
            return api.meta, ()
        if head == "session" and method == "GET":
            return api.session_info, ()
        if head == "login" and method == "POST":
            return api.login, ()
        if head == "logout" and method == "POST":
            return api.logout, ()
        if head == "settings" and method == "POST":
            return api.update_settings, ()
        if head == "dashboard" and method == "GET":
            return api.dashboard, ()
        if head == "log":
            if method == "GET" and not rest:
                return api.log, ()
            if method == "POST" and rest == ["clear"]:
                return api.clear_log, ()
        if head == "lookup" and method == "GET" and rest:
            if rest[0] == "tanks":
                return api.tanks, ()
            if rest[0] == "gtip":
                return api.gtip, ()
        if head == "table" and rest:
            table_key, action = rest[0], (rest[1] if len(rest) > 1 else "")
            if method == "GET" and not action:
                return api.list_records, (table_key,)
            if method == "GET" and action == "services":
                return api.list_services, (table_key,)
            if method == "POST" and not action:
                return api.save_record, (table_key,)
            if method == "PUT" and not action:
                return api.update_record, (table_key,)
            if method == "POST" and action == "delete":
                return api.delete_record, (table_key,)
            if method == "POST" and action == "bulk":
                return api.bulk_save, (table_key,)
            if method == "POST" and action == "validate":
                return api.validate_record, (table_key,)

        raise ApiError(f"Bilinmeyen uç: {method} {path}", status=404)

    # ------------------------------------------------------------------
    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        if length > 8 * 1024 * 1024:
            raise ApiError("İstek gövdesi çok büyük.", status=413)
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ApiError("Geçersiz JSON gövdesi.") from exc
        return data if isinstance(data, dict) else {"value": data}

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _serve_static(self, path: str) -> None:
        relative = posixpath.normpath(unquote(path)).lstrip("/")
        if not relative or relative == ".":
            relative = "index.html"
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._send_json({"error": "Erişim reddedildi."}, status=403)
            return
        if not target.is_file():
            # Tek sayfa uygulaması: bilinmeyen yol arayüze yönlendirilir.
            target = WEB_ROOT / "index.html"
            if not target.is_file():
                self._send_json({"error": "Arayüz dosyaları bulunamadı."}, status=404)
                return

        content = target.read_bytes()
        mime, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(content)
        except BrokenPipeError:
            pass


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: Tuple[str, int], state: AppState, verbose: bool = False):
        self.state = state
        self.verbose = verbose
        handler = type("BoundHandler", (Handler,), {"api": Api(state), "state": state})
        super().__init__(address, handler)


def create_server(host: str = "127.0.0.1", port: int = 8787,
                  verbose: bool = False) -> AppServer:
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("image/svg+xml", ".svg")
    return AppServer((host, port), AppState(), verbose=verbose)
