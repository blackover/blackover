# -*- coding: utf-8 -*-
"""EPDK Petrol Piyasası Stok İzleme — masaüstü/yerel web uygulaması.

Bağımlılık gerektirmez; yalnızca Python standart kütüphanesi kullanılır.
Windows'ta Python hiç gerekmez: epdk/BASLAT.bat, Windows ile hazır gelen
.NET Framework'ü kullanan kurulumsuz sürümü (epdk/windows) çalıştırır.
"""

import sys

# Bu dosya bilerek eski söz dizimiyle yazılmıştır: Python 3.7'den eski bir
# sürümde anlaşılmaz bir SyntaxError yerine açıklayıcı bir mesaj gösterilir.
if sys.version_info < (3, 7):
    sys.stderr.write(
        "\nHATA: Bu uygulama Python 3.7 veya ustunu gerektirir "
        "(bulunan: %d.%d).\n"
        "Windows'ta Python gerekmez: epdk\\BASLAT.bat dosyasina cift tiklayin.\n\n"
        % (sys.version_info[0], sys.version_info[1])
    )
    raise SystemExit(1)

__version__ = "1.0.0"
APP_NAME = "EPDK Petrol Stok İzleme"
