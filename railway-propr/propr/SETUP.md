# Propr Setup Guide

## Step 1: Create Account
Go to https://app.propr.xyz and sign in with Google.

## Step 2: Get API Key
1. Go to https://app.propr.xyz/settings
2. Click "Generate API Key"
3. Copy the key (starts with `pk_live_`)
4. Paste it in the `.env` file in the root of your project

## Step 3: Start Free Trial
1. Go to https://app.propr.xyz/dashboard
2. Click "Get Started" on the Free Trial
3. Complete checkout (no cost)

## Step 4: Run the Bot
```bash
python -m core.exchange.propr.challenge_passer
```

## Step 5: Monitor Progress
The bot will scan for signals every 5 minutes and execute trades automatically.
It will log:
- Account equity and profit
- Daily loss usage
- Max drawdown usage
- Trade signals and executions

## Risk Limits
- Max daily loss: 3% ($150 on $5K account)
- Max drawdown: 6% ($300 static)
- Target: 10% ($500)
- Leverage: BTC/ETH 5x, other crypto 2x

## Important Notes
- The bot uses conservative risk management (2.5% daily limit)
- It will pause trading if drawdown exceeds 5%
- Stop losses are set at 1.5x ATR from entry
- Take profits are set at 3.0x ATR from entry
