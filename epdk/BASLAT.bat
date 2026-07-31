@echo off
REM ============================================================
REM  EPDK Petrol Stok Izleme - Windows baslatici
REM  Bu dosyaya cift tiklayin; uygulama tarayicida acilir.
REM ============================================================
setlocal
cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  HATA: Python bulunamadi.
  echo  https://www.python.org/downloads/ adresinden Python 3.9 veya
  echo  ustunu kurun ve kurulum sirasinda "Add Python to PATH" secenegini
  echo  isaretleyin.
  echo.
  pause
  exit /b 1
)

echo.
echo   EPDK Petrol Stok Izleme baslatiliyor...
echo   Kapatmak icin bu pencerede Ctrl+C tuslayin.
echo.
python -m epdk %*
pause
