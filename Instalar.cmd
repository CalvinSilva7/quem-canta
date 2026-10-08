@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Quem Canta - instalacao

echo ============================================================
echo  Quem Canta - instalacao
echo  Leva de 5 a 15 minutos e precisa de internet.
echo  Nao feche esta janela ate aparecer "INSTALACAO CONCLUIDA".
echo ============================================================
echo.

rem --- 1. Python 3.11 ou mais novo -----------------------------------------
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (python -c "import sys" >nul 2>nul && set "PY=python")
if not defined PY goto :sem_python
%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :python_antigo
for /f "delims=" %%v in ('%PY% -c "import platform; print(platform.python_version())"') do echo [1/4] Python %%v encontrado.

rem --- 2. Ambiente separado, so para o app ----------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [2/4] Criando o ambiente do app...
    %PY% -m venv .venv
    if errorlevel 1 goto :erro
) else (
    echo [2/4] Ambiente do app ja existe.
)

rem --- 3. Bibliotecas --------------------------------------------------------
echo [3/4] Instalando as bibliotecas (a parte mais demorada)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 goto :erro

rem --- 4. Navegador que o app usa para ler as plataformas --------------------
echo [4/4] Baixando o navegador do app (cerca de 150 MB)...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :erro

rem --- Atalho na area de trabalho (se nao der, nao e erro) -------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Quem Canta.lnk'); $a.TargetPath=(Join-Path (Get-Location) 'Abrir Quem Canta.cmd'); $a.WorkingDirectory=(Get-Location).Path; $a.Save()" >nul 2>nul

echo.
echo ============================================================
echo  INSTALACAO CONCLUIDA
echo  Para usar: de dois cliques em "Abrir Quem Canta.cmd"
echo  (ou no atalho "Quem Canta" da area de trabalho).
echo ============================================================
echo.
pause
exit /b 0

:sem_python
echo.
echo O Python nao esta instalado neste computador.
where winget >nul 2>nul
if errorlevel 1 goto :sem_winget
echo Vou instalar o Python agora (pode aparecer um pedido de permissao).
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo Feito. FECHE esta janela e de dois cliques em "Instalar.cmd" de novo.
pause
exit /b 1

:sem_winget
echo Baixe o Python em https://www.python.org/downloads/windows/
echo Na instalacao, marque a opcao "Add python.exe to PATH".
echo Depois, de dois cliques em "Instalar.cmd" de novo.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:python_antigo
echo.
echo O Python deste computador e antigo. O app precisa da versao 3.11 ou mais nova.
echo Baixe em https://www.python.org/downloads/windows/ e rode "Instalar.cmd" de novo.
pause
exit /b 1

:erro
echo.
echo ============================================================
echo  A INSTALACAO NAO TERMINOU.
echo  Confira a internet e rode "Instalar.cmd" de novo: ele continua
echo  de onde parou. Se o erro se repetir, tire uma foto desta janela
echo  e mande para quem cuida do app. Em rede de escritorio, antivirus
echo  ou proxy as vezes bloqueiam os downloads.
echo ============================================================
pause
exit /b 1
