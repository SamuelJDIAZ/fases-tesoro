@echo off
REM Doble clic para abrir la app "Fases del Tesoro" en el navegador
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo No encontre Python. Instalalo desde https://www.python.org/downloads/ y vuelve a intentar.
  pause
  exit /b 1
)
echo Usando Python: %PY%
echo Instalando/verificando librerias (solo tarda la primera vez)...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo Abriendo la app en el navegador (http://localhost:8501). Para cerrarla, cierra esta ventana.
%PY% -m streamlit run app.py
pause
