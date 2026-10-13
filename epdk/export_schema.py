"""Şema dışa aktarımı:  python -m epdk.export_schema

``epdk/schema.py`` tek doğruluk kaynağıdır. Windows için yazılmış .NET
sunucusu (``epdk/windows/EpdkStok.cs``) Python'a ihtiyaç duymadan aynı
alan tanımlarını kullanabilsin diye şema ``epdk/web/schema.json`` dosyasına
yazılır. Test paketi bu dosyanın güncel olduğunu denetler; şema değişince bu
komutu çalıştırmak yeterlidir.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ENVIRONMENTS
from .schema import describe

SCHEMA_PATH = Path(__file__).parent / "web" / "schema.json"


def render() -> str:
    payload = {
        "_note": "Bu dosya otomatik üretilir: python -m epdk.export_schema",
        "environments": ENVIRONMENTS,
        **describe(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    SCHEMA_PATH.write_text(render(), encoding="utf-8")
    print(f"yazıldı: {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
