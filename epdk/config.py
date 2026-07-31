"""Uygulama ayarları ve dosya konumları.

Ayarlar kullanıcının ev dizinindeki ``~/.epdk-stok`` klasöründe tutulur.
Parola **hiçbir zaman** diske yazılmaz; yalnızca çalışan oturumun belleğinde
saklanır.
"""

from __future__ import annotations

import json
import os
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


def resolve_base_url(settings: Dict[str, Any]) -> str:
    """Seçili ortama göre kullanılacak kök adresi döndürür."""
    env = settings.get("environment", "test")
    if env == "custom":
        return (settings.get("custom_base_url") or "").rstrip("/")
    return ENVIRONMENTS.get(env, ENVIRONMENTS["test"])["base_url"].rstrip("/")
