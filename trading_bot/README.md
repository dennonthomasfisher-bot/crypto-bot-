# Crypto Trading Bot

An automated Python trading bot for the **Crypto.com Exchange** that combines four independent strategies into a single signal score and manages risk through configurable limits.

---

## Features

| Feature | Detail |
|---|---|
| Exchange | Crypto.com Exchange REST API v1 |
| Coins | BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOGE, DOT, MATIC |
| Strategies | RSI · Momentum/breakout · DCA · News sentiment |
| Risk controls | Daily spend cap · Max per trade · Max position size · Stop-loss · Take-profit |
| Default mode | **Dry run** (no real orders placed until you opt in) |

---

## Strategies

### 1. RSI — overbought / oversold (weight 30%)
Uses Wilder's smoothed RSI over a configurable period (default 14).
- RSI ≤ 30 → **+1.0** (buy signal)
- RSI ≥ 70 → **−1.0** (sell signal)
- Otherwise → 0.0 (neutral)

### 2. Momentum / breakout (weight 25%)
Fires when the latest hourly close exceeds the 20-period high by at least 3 %.
- Breakout confirmed → **+1.0**
- No breakout → 0.0

### 3. DCA — dollar-cost averaging (weight 20%)
Automatically buys a fixed USD amount (`DCA_AMOUNT_USD`) every 24 hours for BTC, ETH and SOL.
DCA also contributes **+0.5** to the combined signal score when a buy is due, nudging the score toward BUY.
The DCA buy fires **independently** of the signal score — as long as the daily budget allows.

### 4. News sentiment (weight 25%)
Parses the latest headlines and summaries from three RSS feeds:
- [CoinTelegraph](https://cointelegraph.com/rss)
- [CoinDesk](https://www.coindesk.com/arc/outboundfeeds/rss/)
- [Bitcoin Magazine](https://bitcoinmagazine.com/.rss/full/)

Counts bullish vs bearish keyword occurrences and returns a score in [−1, +1].
Feed results are cached for 10 minutes to avoid excessive requests.

### Combined signal score
```
score = 0.30 × rsi + 0.25 × momentum + 0.20 × dca + 0.25 × sentiment
```
- `score ≥ SIGNAL_BUY_THRESHOLD`  (default **0.30**) → **BUY**
- `score ≤ SIGNAL_SELL_THRESHOLD` (default **−0.30**) → **SELL**
- Otherwise → **HOLD**

---

## Risk management

All limits are enforced **before** any order is placed:

| Parameter | Default | Description |
|---|---|---|
| `DAILY_SPEND_CAP` | $100 | Hard ceiling per calendar day across all pairs |
| `MAX_PER_TRADE` | $25 | Maximum USD for a single market order |
| `MAX_POSITION_PCT` | 10% | Maximum fraction of available USDT balance per position |
| `STOP_LOSS_PCT` | 5% | Auto-sell when price drops 5% below entry |
| `TAKE_PROFIT_PCT` | 8% | Auto-sell when price rises 8% above entry |

Stop-loss and take-profit are evaluated at the **start** of every polling cycle before new signals are computed.

---

## Project structure

```
trading_bot/
├── bot.py                  # Main entry point and trading loop
├── config.py               # Environment-variable driven configuration
├── exchange.py             # Crypto.com Exchange API v1 client
├── risk_manager.py         # Daily spend cap, position sizing, SL/TP
├── signal_aggregator.py    # Weighted signal combination
├── strategies/
│   ├── __init__.py
│   ├── rsi.py              # RSI overbought/oversold signal
│   ├── momentum.py         # Breakout above N-period high
│   ├── dca.py              # Dollar-cost averaging timer
│   └── sentiment.py        # RSS news sentiment analyser
├── .env.example            # Configuration template
├── requirements.txt
└── README.md
```

---

## Quick start

### 1. Clone / navigate to the directory
```bash
cd trading_bot
```

### 2. Create a virtual environment
```bash
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure the bot
```bash
cp .env.example .env
```
Edit `.env` and set at minimum:
- `DRY_RUN=true` (keep this while testing)
- `CRYPTO_COM_API_KEY` and `CRYPTO_COM_API_SECRET` (required for live mode)

### 5. Run in dry-run mode (default)
```bash
python bot.py
```

The bot will log every signal calculation and simulated order without touching real funds.

### 6. Enable live trading
Once you are satisfied with the bot's behaviour in dry-run mode:
```
DRY_RUN=false
```
> **Warning:** Live trading involves real financial risk.
> Start with small limits (`DAILY_SPEND_CAP`, `MAX_PER_TRADE`) and monitor closely.

---

## Getting Crypto.com API keys

1. Log in to [Crypto.com Exchange](https://crypto.com/exchange)
2. Navigate to **User menu → API Management**
3. Create a new key with **Spot Trading** permissions
4. Set an IP whitelist for your server's IP address
5. Copy the key and secret into your `.env` file

> **Never** share your API secret or commit `.env` to version control.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `CRYPTO_COM_API_KEY` | *(required for live)* | API key from Crypto.com Exchange |
| `CRYPTO_COM_API_SECRET` | *(required for live)* | Matching API secret |
| `DRY_RUN` | `true` | `false` to enable live trading |
| `TRADING_PAIRS` | 10 major coins | Comma-separated instrument names |
| `DAILY_SPEND_CAP` | `100` | Max USD per calendar day |
| `MAX_PER_TRADE` | `25` | Max USD per single order |
| `MAX_POSITION_PCT` | `0.10` | Max fraction of balance per position |
| `STOP_LOSS_PCT` | `0.05` | Stop-loss trigger (5%) |
| `TAKE_PROFIT_PCT` | `0.08` | Take-profit trigger (8%) |
| `RSI_PERIOD` | `14` | RSI lookback period |
| `RSI_OVERSOLD` | `30` | RSI buy threshold |
| `RSI_OVERBOUGHT` | `70` | RSI sell threshold |
| `MOMENTUM_PERIOD` | `20` | Candles for breakout reference high |
| `MOMENTUM_THRESHOLD` | `0.03` | Required excess above period high (3%) |
| `DCA_INTERVAL_HOURS` | `24` | Hours between DCA buys |
| `DCA_PAIRS` | BTC/ETH/SOL | Pairs to DCA into |
| `DCA_AMOUNT_USD` | `10` | Fixed USD per DCA buy |
| `SIGNAL_BUY_THRESHOLD` | `0.30` | Min score to trigger a BUY |
| `SIGNAL_SELL_THRESHOLD` | `-0.30` | Max score to trigger a SELL |
| `POLL_INTERVAL_SECONDS` | `60` | Seconds between full evaluation cycles |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose output |

---

## Disclaimer

This software is provided for educational and informational purposes only. Automated cryptocurrency trading carries significant financial risk. Past performance of any strategy does not guarantee future results. Always test thoroughly in dry-run mode before deploying real capital. The authors accept no liability for financial losses.
