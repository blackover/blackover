"""Oturum yönetimi.

Token bellekte tutulur; parola yalnızca "otomatik yenileme" açıkken ve yine
sadece bellekte saklanır — hiçbir koşulda diske yazılmaz.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from .client import EpdkClient, EpdkError

# Token bitimine bu kadar saniye kalınca otomatik yenileme denenir.
RENEW_MARGIN_SECONDS = 300


class Session:
    """Uygulamanın açık EPDK oturumu."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.token: str = ""
        self.username: str = ""
        self.claims: Dict[str, Any] = {}
        self.expires_at: Optional[int] = None
        self.base_url: str = ""
        self.environment: str = ""
        self.timeout: int = 45
        self.verify_ssl: bool = True
        self.logged_in_at: Optional[float] = None
        self._password: str = ""
        self._auto_renew: bool = False
        # Sık kullanılan referans listeleri için basit önbellek
        self._cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.token)

    @property
    def licences(self) -> List[str]:
        raw = self.claims.get("licenceNumber") or ""
        return [part.strip() for part in str(raw).split(",") if part.strip()]

    def seconds_left(self) -> Optional[int]:
        if not self.expires_at:
            return None
        return max(0, int(self.expires_at - time.time()))

    def client(self) -> EpdkClient:
        return EpdkClient(self.base_url, timeout=self.timeout, verify_ssl=self.verify_ssl)

    # ------------------------------------------------------------------
    def login(
        self,
        *,
        base_url: str,
        environment: str,
        username: str,
        password: str,
        timeout: int = 45,
        verify_ssl: bool = True,
        auto_renew: bool = True,
    ) -> Dict[str, Any]:
        client = EpdkClient(base_url, timeout=timeout, verify_ssl=verify_ssl)
        info = client.login(username, password)
        with self._lock:
            self.token = info["token"]
            self.username = username
            self.claims = info.get("claims") or {}
            self.expires_at = info.get("expiresAt")
            self.base_url = base_url.rstrip("/")
            self.environment = environment
            self.timeout = timeout
            self.verify_ssl = verify_ssl
            self.logged_in_at = time.time()
            self._auto_renew = auto_renew
            self._password = password if auto_renew else ""
            self._cache.clear()
        return self.describe()

    def logout(self) -> None:
        with self._lock:
            self.token = ""
            self.claims = {}
            self.expires_at = None
            self._password = ""
            self.logged_in_at = None
            self._cache.clear()

    def ensure_token(self) -> str:
        """Geçerli token'ı döndürür; süresi dolmak üzereyse yeniler."""
        with self._lock:
            if not self.token:
                raise EpdkError("Oturum açık değil. Lütfen giriş yapın.")
            remaining = self.seconds_left()
            needs_renew = remaining is not None and remaining <= RENEW_MARGIN_SECONDS
            can_renew = self._auto_renew and self._password

            if needs_renew and can_renew:
                client = EpdkClient(
                    self.base_url, timeout=self.timeout, verify_ssl=self.verify_ssl
                )
                info = client.login(self.username, self._password)
                self.token = info["token"]
                self.claims = info.get("claims") or {}
                self.expires_at = info.get("expiresAt")
            elif remaining == 0:
                raise EpdkError(
                    "Oturum süresi doldu (EPDK token ömrü 60 dakikadır). "
                    "Lütfen yeniden giriş yapın."
                )
            return self.token

    # --------------------------------------------------------- önbellek
    def cached(self, key: str, ttl: int = 600) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() - entry["at"] > ttl:
            return None
        return entry["value"]

    def cache(self, key: str, value: Any) -> Any:
        self._cache[key] = {"at": time.time(), "value": value}
        return value

    def invalidate(self, key: str = "") -> None:
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()

    # ------------------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "username": self.username,
            "environment": self.environment,
            "baseUrl": self.base_url,
            "licences": self.licences,
            "authority": self.claims.get("authority"),
            "expiresAt": self.expires_at,
            "secondsLeft": self.seconds_left(),
            "autoRenew": bool(self._auto_renew and self._password),
            "loggedInAt": self.logged_in_at,
        }
