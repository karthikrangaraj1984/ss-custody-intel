@echo off
REM Start the Custody Market Monitor web app
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m streamlit run app.py
