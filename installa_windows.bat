@echo off
rem Installa Moviola su Windows: doppio clic su questo file.
rem - copia il programma in  %LOCALAPPDATA%\Moviola\app
rem - prepara le librerie (serve internet, qualche minuto la prima volta)
rem - crea i collegamenti "Moviola" sul Desktop e nel menu Start, con la sua icona
rem Si puo' rilanciare quando si vuole: reinstalla sopra, senza toccare progetti e impostazioni.
cd /d "%~dp0"
if not exist "moviola\__init__.py" (
  echo Lancia questo file dalla cartella di Moviola ^(quella con dentro la cartella "moviola"^).
  echo Se hai scaricato lo ZIP, prima estrailo: tasto destro, Estrai tutto.
  goto errore
)
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul || (
  echo Python 3 non trovato. Installalo da https://www.python.org/downloads/
  echo ^(spunta "Add python.exe to PATH"^) e rilancia questo file.
  goto errore
)

set "BASE=%LOCALAPPDATA%\Moviola"
set "DEST=%BASE%\app"
set "VENV=%BASE%\venv"
echo 1/3  Copio il programma in: %DEST%
if not exist "%DEST%" mkdir "%DEST%"
robocopy "%~dp0." "%DEST%" /MIR /XD .git __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo Copia non riuscita.
  goto errore
)

echo 2/3  Preparo le librerie ^(la prima volta ci vuole qualche minuto^)...
if not exist "%VENV%\Scripts\python.exe" (
  %PY% -m venv "%VENV%" || goto errore
)
fc /b "%DEST%\requirements.txt" "%VENV%\requirements.installati.txt" >nul 2>nul
if errorlevel 1 (
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -r "%DEST%\requirements.txt" || goto errore
  copy /y "%DEST%\requirements.txt" "%VENV%\requirements.installati.txt" >nul
)

echo 3/3  Creo i collegamenti...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $d 'Moviola.lnk'));" ^
  "  $s.TargetPath = Join-Path $env:LOCALAPPDATA 'Moviola\venv\Scripts\pythonw.exe';" ^
  "  $s.Arguments = '-m moviola';" ^
  "  $s.WorkingDirectory = Join-Path $env:LOCALAPPDATA 'Moviola\app';" ^
  "  $s.IconLocation = (Join-Path $env:LOCALAPPDATA 'Moviola\app\moviola\risorse\icona.ico') + ',0';" ^
  "  $s.Description = 'Moviola - montaggio video';" ^
  "  $s.Save() }"
if errorlevel 1 (
  echo Non sono riuscito a creare i collegamenti: puoi avviare Moviola da
  echo %DEST%\avvia_windows.bat
)

echo.
echo Fatto: Moviola e' installata. La trovi sul Desktop e nel menu Start.
echo Questa cartella scaricata ora si puo' eliminare.
echo.
set /p R=Vuoi aprire Moviola adesso? [S/n] 
if /i not "%R%"=="n" start "" /d "%DEST%" "%VENV%\Scripts\pythonw.exe" -m moviola
exit /b 0

:errore
echo.
echo Installazione non completata.
pause
exit /b 1
