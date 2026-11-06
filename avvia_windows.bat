@echo off
rem Avvio di Moviola su Windows: doppio clic su questo file.
rem Al primo avvio crea un ambiente Python dedicato e installa le librerie
rem (serve internet, qualche minuto). Le volte successive parte subito.
cd /d "%~dp0"

set "VENV=%LOCALAPPDATA%\Moviola\venv"
set "PY=py -3"
where py >nul 2>nul || set "PY=python"

if not exist "%VENV%\Scripts\python.exe" (
  echo Primo avvio: preparo l'ambiente Python...
  if not exist "%LOCALAPPDATA%\Moviola" mkdir "%LOCALAPPDATA%\Moviola"
  %PY% -m venv "%VENV%" || goto errore
)

fc /b requirements.txt "%VENV%\requirements.installati.txt" >nul 2>nul
if errorlevel 1 (
  echo Installo/aggiorno le librerie ^(PySide6, PyAV, NumPy, FFmpeg...^)...
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt || goto errore
  copy /y requirements.txt "%VENV%\requirements.installati.txt" >nul
)

start "" "%VENV%\Scripts\pythonw.exe" -m moviola %*
exit /b 0

:errore
echo.
echo Qualcosa non e' andato a buon fine. Controlla che Python 3 sia installato
echo (https://www.python.org/downloads/ - spunta "Add python.exe to PATH") e la connessione.
pause
exit /b 1
