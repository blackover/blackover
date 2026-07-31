"""Uygulamayı başlatır:  python -m epdk"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import APP_NAME, __version__
from .config import app_home
from .server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="epdk",
        description=f"{APP_NAME} — yerel masaüstü arayüzü",
    )
    parser.add_argument("--port", type=int, default=8787, help="dinlenecek port (varsayılan 8787)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="dinlenecek adres (varsayılan 127.0.0.1 — yalnızca bu bilgisayar)")
    parser.add_argument("--no-browser", action="store_true", help="tarayıcıyı otomatik açma")
    parser.add_argument("--verbose", action="store_true", help="istek günlüğünü göster")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    try:
        server = create_server(args.host, args.port, verbose=args.verbose)
    except OSError as exc:
        print(f"HATA: {args.host}:{args.port} dinlenemedi → {exc}", file=sys.stderr)
        print("Başka bir port deneyin:  python -m epdk --port 8788", file=sys.stderr)
        return 1

    url = f"http://{args.host}:{args.port}/"
    print(f"\n  {APP_NAME} v{__version__}")
    print(f"  ➜  Arayüz : {url}")
    print(f"  ➜  Ayarlar: {app_home()}")
    print("  ➜  Durdurmak için Ctrl+C\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor…")
    finally:
        server.shutdown()
        server.server_close()
        server.state.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
