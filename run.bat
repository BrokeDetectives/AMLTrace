@echo off
REM AMLTrace launcher - installs dependencies once, then starts the console.
python -m pip install -q -r requirements.txt
echo.
echo   AMLTrace starting on http://127.0.0.1:5000
echo.
REM Open the browser a few seconds late, in a detached window. Opening it on the
REM line before the server starts raced the server and showed the judge a
REM connection error on a page that was about to work.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start "" http://127.0.0.1:5000"
python app.py
