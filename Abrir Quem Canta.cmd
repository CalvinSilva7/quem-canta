@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Quem Canta - deixe esta janela aberta enquanto usa o app

if not exist ".venv\Scripts\python.exe" (
    echo O app ainda nao foi instalado neste computador.
    echo De dois cliques em "Instalar.cmd" primeiro.
    pause
    exit /b 1
)

echo ============================================================
echo  Quem Canta esta abrindo no seu navegador:
echo      http://localhost:8502
echo.
echo  DEIXE ESTA JANELA ABERTA enquanto usa o app.
echo  Para encerrar o app (e parar uma coleta), feche esta janela.
echo ============================================================
echo.

rem Abre o navegador alguns segundos depois, quando o app ja estiver no ar.
start "" /b cmd /c "timeout /t 6 /nobreak >nul & start "" http://localhost:8502"
".venv\Scripts\python.exe" -m streamlit run creditos_app.py --server.headless true --server.port 8502 --server.address localhost --browser.gatherUsageStats false

echo.
echo O app foi encerrado.
pause
