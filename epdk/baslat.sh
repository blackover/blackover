#!/usr/bin/env bash
# EPDK Petrol Stok İzleme — macOS / Linux başlatıcı
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "HATA: Python bulunamadı. Python 3.9 veya üstünü kurun." >&2
  exit 1
fi

echo
echo "  EPDK Petrol Stok İzleme başlatılıyor…"
echo "  Kapatmak için Ctrl+C."
echo
exec "$PY" -m epdk "$@"
