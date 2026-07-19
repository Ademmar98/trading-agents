@echo off
rem Dedicated METALS instance: trades XAUUSD/XAGUSD on the MT5 demo account
rem (leverage 1:1) while the main instance keeps paper-trading everything.
rem Credentials come from .env (MT5_LOGIN / MT5_PASSWORD / MT5_SERVER).
cd /d "%~dp0"
set BROKER_TYPE=mt5
set MARKET_TYPE=metals
set TRADING_DATA_DIR=data_metals
set PORT=8001
set TRADING_LOCK_PORT=48720
set TRADING_CAPITAL=100000
python main.py %*
