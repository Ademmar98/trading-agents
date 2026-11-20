@echo off
rem Propr bot - local PC launcher (dry by default; live when given --live)
cd /d "%~dp0"
python bot.py %*