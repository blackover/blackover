"""Uygulama ayarları ve dosya konumları.

Ayarlar kullanıcının ev dizinindeki ``~/.epdk-stok`` klasöründe tutulur.
Parola **hiçbir zaman** diske yazılmaz; yalnızca çalışan oturumun belleğinde
saklanır.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# EPDK ortamları. Gerçek ortam adresi kullanıcı tarafından verilmiştir.
ENVIRONMENTS = {
    "prod": {
        "label_tr": "Gerçek Ortam",
        "label_en": "Production",
        "base_url": "https://petrolstok.epdk.gov.tr/petrolstok/api",
    },
    "test": {
        "label_tr": "Test Ortamı",
        "label_en": "Test",
        "base_url": "https://petrolstok-test.epdk.gov.tr/petrolstok/api",
    },
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "environment": "test",          # güvenli taraf: varsayılan test ortamı
    "custom_base_url": "",
    "username": "",                 # son kullanılan kullanıcı adı (parola değil)
    "profiles": [],                 # kayıtlı müşteriler (parola içermez)
    "language": "tr",
    "theme": "light",
    "timeout": 45,                  # saniye
    "auto_renew": True,             # token bitmeden önce otomatik yenile
    "confirm_delete": True,
    "log_limit": 2000,              # yerel kayıt defterinde tutulacak satır sayısı
    "gumruk_labels": {
        "0": "Millileşmiş (serbest dolaşımda)",
        "1": "Gümrüklü (antrepo)",
    },
}


def app_home() -> Path:
    """Ayar ve veritabanı dosyalarının tutulduğu dizin."""
    override = os.environ.get("EPDK_APP_HOME")
    base = Path(override) if override else Path.home() / ".epdk-stok"
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_path() -> Path:
    return app_home() / "settings.json"


def db_path() -> Path:
    return app_home() / "activity.db"


def load_settings() -> Dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    data["gumruk_labels"] = dict(DEFAULT_SETTINGS["gumruk_labels"])
    path = settings_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return data
        if isinstance(stored, dict):
            stored.pop("password", None)  # parola asla saklanmaz
            for key, value in stored.items():
                if key in data:
                    data[key] = value
    return data


def save_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    for key, value in (values or {}).items():
        if key in DEFAULT_SETTINGS:
            current[key] = value
    current.pop("password", None)
    settings_path().write_text(
        json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return current


MAX_PROFILES = 20


def upsert_profile(settings: Dict[str, Any], *, username: str, environment: str,
                   custom_base_url: str = "") -> Dict[str, Any]:
    """Başarılı girişten sonra müşteriyi hızlı geçiş listesine ekler.

    Yalnızca kullanıcı adı, ortam ve (özel ortamsa) adres saklanır —
    **parola hiçbir zaman yazılmaz.**
    """
    profiles = [dict(item) for item in settings.get("profiles", [])
                if isinstance(item, dict)]
    key = (username, environment)
    profiles = [item for item in profiles
                if (item.get("username"), item.get("environment")) != key]

    profiles.insert(0, {
        "username": username,
        "environment": environment,
        "custom_base_url": custom_base_url if environment == "custom" else "",
        "licence": username[4:] if username.startswith("WSU-") else username,
        "last_used": datetime.now().isoformat(timespec="seconds"),
    })
    settings["profiles"] = profiles[:MAX_PROFILES]
    return settings


def remove_profile(settings: Dict[str, Any], *, username: str,
                   environment: str) -> Dict[str, Any]:
    settings["profiles"] = [
        item for item in settings.get("profiles", [])
        if not (item.get("username") == username
                and item.get("environment") == environment)
    ]
    return settings


def resolve_base_url(settings: Dict[str, Any]) -> str:
    """Seçili ortama göre kullanılacak kök adresi döndürür."""
    env = settings.get("environment", "test")
    if env == "custom":
        return (settings.get("custom_base_url") or "").rstrip("/")
    return ENVIRONMENTS.get(env, ENVIRONMENTS["test"])["base_url"].rstrip("/")
