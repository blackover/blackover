@echo off
REM ======================================================================
REM  EPDK Petrol Stok Izleme - Windows baslatici
REM
REM  Bu dosyaya cift tiklayin. KURULUM GEREKMEZ:
REM    * Python gerekmez.
REM    * Internetten dosya indirilmez, yonetici yetkisi istenmez.
REM
REM  Windows 8 / Windows Server 2012 ve sonrasinda hazir gelen .NET
REM  Framework 4.5 icindeki C# derleyicisi (csc.exe) ile uygulama her
REM  acilista windows\EpdkStok.cs dosyasindan derlenir (1-3 saniye) ve
REM  calistirilir. Her acilista derlendigi icin guncelleme sonrasi eski
REM  surumun calismasi gibi bir sorun olusmaz.
REM
REM  Secenekler:  BASLAT.bat --port 8788      (farkli port)
REM               BASLAT.bat --no-browser    (tarayiciyi acma)
REM ======================================================================
setlocal EnableExtensions
title EPDK Petrol Stok Izleme
cd /d "%~dp0"

set "SRC=%~dp0windows\EpdkStok.cs"
set "OUT=%~dp0windows\EpdkStok.exe"
set "NEW=%~dp0windows\EpdkStok.new.exe"
set "LOG=%TEMP%\epdk-derleme.log"

REM --- .NET derleyicisini bul (64 bit oncelikli) ---------------------------
set "CSC="
if exist "%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not defined CSC if exist "%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not defined CSC goto :nodotnet

echo.
echo   EPDK Petrol Stok Izleme hazirlaniyor...

"%CSC%" /nologo /optimize+ /target:exe /out:"%NEW%" /r:System.Web.Extensions.dll /r:System.Core.dll "%SRC%" > "%LOG%" 2>&1
if errorlevel 1 goto :buildfailed

REM Uygulama zaten aciksa eski exe kilitlidir; o durumda acik olan kullanilir.
move /y "%NEW%" "%OUT%" >nul 2>&1
if exist "%NEW%" del /q "%NEW%" >nul 2>&1
goto :run

:buildfailed
if exist "%NEW%" del /q "%NEW%" >nul 2>&1
if not exist "%OUT%" goto :buildfatal
echo.
echo   UYARI: Derleme basarisiz oldu; onceki surum calistiriliyor.
echo   Ayrinti: "%LOG%"
goto :run

:buildfatal
echo.
echo   HATA: Uygulama derlenemedi. Derleyici ciktisi:
echo.
type "%LOG%"
echo.
pause
exit /b 1

:run
"%OUT%" %*
if errorlevel 1 pause
exit /b %errorlevel%

REM --- .NET yoksa Python denenir --------------------------------------------
:nodotnet
echo.
echo   .NET Framework 4.5 bulunamadi; Python deneniyor...
where py >nul 2>nul
if errorlevel 1 goto :trypython
cd /d "%~dp0.."
py -3 -m epdk %*
goto :pythondone

:trypython
where python >nul 2>nul
if errorlevel 1 goto :nothing
cd /d "%~dp0.."
python -m epdk %*

:pythondone
if errorlevel 1 pause
exit /b %errorlevel%

:nothing
echo.
echo   HATA: Ne .NET Framework 4.5 ne de Python bulunabildi.
echo.
echo   - Windows 8, 10, 11 ve Windows Server 2012 ve sonrasinda
echo     .NET Framework 4.5 hazir gelir; bu mesaji gormemeniz gerekir.
echo   - Windows 7 / Server 2008 R2 icin .NET Framework 4.5 (veya ustu)
echo     gerekir; genellikle Windows Update ile zaten yuklenmistir.
echo   - Alternatif olarak Python 3.7 veya ustu kullanilabilir.
echo.
pause
exit /b 1
