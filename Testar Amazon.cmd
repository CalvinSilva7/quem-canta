@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Quem Canta - teste do aplicativo Amazon Music

if not exist ".venv\Scripts\python.exe" (
    echo O app ainda nao foi instalado neste computador.
    echo De dois cliques em "Instalar.cmd" primeiro.
    pause
    exit /b 1
)

echo ============================================================
echo  Teste do aplicativo Amazon Music
echo.
echo  Antes de continuar:
echo   1. O Amazon Music de desktop precisa estar instalado.
echo   2. Abra o Amazon Music uma vez e confira que a conta do
echo      escritorio esta logada. Depois pode fechar.
echo.
echo  O teste vai fechar e abrir o Amazon Music, abrir um album
echo  de exemplo, o menu de uma faixa e a janela de creditos.
echo  Nao le dados da conta e nao altera nada.
echo ============================================================
echo.
pause

".venv\Scripts\python.exe" -m creditos.amazon_app

echo.
echo ============================================================
echo  Pronto. Mande para quem cuida do app a pasta:
echo      Documentos\Quem Canta\diagnostico-amazon
echo  (o arquivo de texto e as imagens que estiverem la).
echo ============================================================
pause
