@echo off
rem Sobe o app e abre o navegador em http://localhost:8501. Feche esta janela para encerrar.
cd /d "%~dp0"
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app.py
