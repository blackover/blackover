"""EPDK Petrol Stok İzleme web servis istemcisi.

Servisin sözleşmesi (kılavuz Bölüm 2–3):

* Tüm yanıtlar ``{"success": bool, "message": str|null, "data": [...] }``
  biçimindedir.
* ``authentication/login`` başarılı olduğunda **token, ``message`` alanının
  içinde** döner.
* Sorgu (``*sorgu``) metotları **GET** olmasına rağmen gövdede
  ``{"kullanici": "..."}`` bekler.
* Token ömrü 60 dakikadır ve ``Authorization: Bearer <token>`` ile gönderilir.
"""

from __future__ import annotations

import base64
import json
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

USER_AGENT = "EPDK-Stok-Masaustu/1.0"


class EpdkError(Exception):
    """Servis ya da ağ kaynaklı hata."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 payload: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.payload = payload or {}


@dataclass
class ApiResult:
    """Servisten dönen ham yanıt + ölçüm bilgileri."""

    success: bool
    message: Optional[str]
    data: Any
    status: int
    raw: Any
    duration_ms: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "data": self.data,
            "status": self.status,
            "durationMs": self.duration_ms,
        }


def decode_token(token: str) -> Dict[str, Any]:
    """JWT gövdesini çözer (imza doğrulaması yapılmaz — yalnızca bilgi amaçlı)."""
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload + padding)
        return json.loads(decoded.decode("utf-8"))
    except Exception:  # noqa: BLE001 - bozuk token bilgi kaybından ibarettir
        return {}


class EpdkClient:
    """Tek bir EPDK ortamına yapılan çağrıları yönetir."""

    def __init__(self, base_url: str, *, timeout: int = 45, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl

    # ------------------------------------------------------------------ ağ
    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if self.verify_ssl:
            return None
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def request(
        self,
        path: str,
        *,
        method: str = "POST",
        body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> ApiResult:
        url = f"{self.base_url}/{path.strip('/')}"
        payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        # Sorgu metotları GET olduğu hâlde gövde beklediği için gövde her
        # durumda gönderilir.
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)

        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=self._ssl_context()
            ) as response:
                status = response.status
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status = exc.code
            text = exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise EpdkError(
                f"Servise ulaşılamadı: {exc.reason}. Adresi ve internet "
                f"bağlantınızı kontrol edin ({url}).",
            ) from exc
        except TimeoutError as exc:
            raise EpdkError(
                f"Servis {self.timeout} saniye içinde yanıt vermedi ({url})."
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            parsed = json.loads(text) if text.strip() else {}
        except ValueError:
            # Servis bazı hata durumlarında düz metin/HTML döndürebilir.
            if status >= 400:
                raise EpdkError(
                    f"Servis {status} kodu ile yanıt verdi.",
                    status=status,
                    payload={"body": text[:1000]},
                )
            parsed = {"success": True, "message": text.strip(), "data": None}

        if isinstance(parsed, list):
            parsed = {"success": status < 400, "message": None, "data": parsed}
        if not isinstance(parsed, dict):
            parsed = {"success": status < 400, "message": str(parsed), "data": None}

        return ApiResult(
            success=bool(parsed.get("success", status < 400)),
            message=parsed.get("message"),
            data=parsed.get("data"),
            status=status,
            raw=parsed,
            duration_ms=duration_ms,
        )

    # -------------------------------------------------------------- oturum
    def login(self, username: str, password: str) -> Dict[str, Any]:
        """Oturum açar ve token bilgisini döndürür.

        Kılavuz: başarılı yanıtta token ``message`` alanındadır.
        """
        result = self.request(
            "authentication/login",
            method="POST",
            body={"username": username, "password": password},
        )
        if not result.success:
            raise EpdkError(result.message or "Oturum açılamadı.", status=result.status)

        token = self._extract_token(result)
        if not token:
            raise EpdkError(
                "Servis oturum açma yanıtında token bulunamadı.",
                status=result.status,
                payload=result.raw if isinstance(result.raw, dict) else {},
            )

        claims = decode_token(token)
        return {
            "token": token,
            "claims": claims,
            "expiresAt": claims.get("exp"),
            "licence": claims.get("licenceNumber"),
            "userName": claims.get("userName") or username,
            "durationMs": result.duration_ms,
        }

    @staticmethod
    def _extract_token(result: ApiResult) -> str:
        """Token'ı yanıtın bilinen tüm konumlarında arar."""
        candidates = [result.message]
        data = result.data
        if isinstance(data, str):
            candidates.append(data)
        elif isinstance(data, dict):
            candidates.extend(
                data.get(key) for key in ("token", "accessToken", "jwt", "message")
            )
        if isinstance(result.raw, dict):
            candidates.extend(
                result.raw.get(key) for key in ("token", "accessToken", "jwt")
            )
        for candidate in candidates:
            if isinstance(candidate, str):
                value = candidate.strip()
                # JWT: üç bölüm, noktayla ayrılmış
                if value.count(".") == 2 and len(value) > 40:
                    return value
        return ""

    # ------------------------------------------------------------- metotlar
    def query(self, path: str, username: str, token: str) -> ApiResult:
        """``*sorgu`` metotları: gövdesinde kullanıcı olan GET istekleri."""
        return self.request(path, method="GET", body={"kullanici": username}, token=token)

    def save(self, table_path: str, payload: Dict[str, Any], token: str) -> ApiResult:
        return self.request(f"{table_path}/save", method="POST", body=payload, token=token)

    def update(self, table_path: str, payload: Dict[str, Any], token: str) -> ApiResult:
        return self.request(f"{table_path}/update", method="POST", body=payload, token=token)

    def delete(self, table_path: str, record_id: str, username: str, token: str) -> ApiResult:
        return self.request(
            f"{table_path}/delete",
            method="POST",
            body={"id": record_id, "kullanici": username},
            token=token,
        )
