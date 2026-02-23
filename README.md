# Crypto News Twitter Bot

Automatically posts to X (Twitter) whenever big crypto news breaks or a major price move happens. Runs silently in the background on your Mac.

## What it does

- **Price alerts** – polls CoinGecko every 5 minutes; tweets when any tracked coin moves ≥ 5 % in 1 hour or ≥ 10 % in 24 hours.
- **Breaking news** – polls CryptoPanic every 10 minutes; tweets hot / trending news stories as they appear.
- **Deduplication** – never posts the same price alert or story twice within the cooldown window.
- **Mac background service** – one script installs a launchd agent that starts at login and restarts if it crashes.

## Tracked coins (configurable in `config.py`)

BTC · ETH · BNB · SOL · XRP · ADA · DOGE · AVAX · DOT · LINK

---

## Quick start

### 1. Get your API keys

| Service | Where to get it | Cost |
|---------|----------------|------|
| **Twitter / X** | [developer.twitter.com](https://developer.twitter.com/en/portal/dashboard) – create a project, enable OAuth 1.0a Read & Write | Free ("Essential" = 1,500 tweets/month) |
| **CryptoPanic** | [cryptopanic.com/developers/api](https://cryptopanic.com/developers/api/) | Free |
| **CoinGecko** | No key needed for the free public API | Free |

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env and fill in all values
```

### 3. Install and run (Mac)

```bash
chmod +x setup_mac.sh
./setup_mac.sh
```

The script will:
- Create a Python virtual environment and install dependencies
- Write a `~/Library/LaunchAgents/com.cryptobot.plist` service file
- Load the service so the bot starts immediately and at every login

### 4. Verify it's running

```bash
launchctl list | grep cryptobot
tail -f /tmp/cryptobot.out.log
```

---

## Manual / non-Mac usage

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in keys

python bot.py            # run in foreground
python bot.py --dry-run  # print tweets without posting
```

---

## Configuration

All settings are in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `COINS` | 10 major coins | Dict of CoinGecko ID → symbol |
| `PRICE_ALERT_1H_PCT` | 5.0 | % move in 1 h to trigger tweet |
| `PRICE_ALERT_24H_PCT` | 10.0 | % move in 24 h to trigger tweet |
| `PRICE_CHECK_INTERVAL` | 300 s | How often to poll CoinGecko |
| `NEWS_CHECK_INTERVAL` | 600 s | How often to poll CryptoPanic |
| `PRICE_ALERT_COOLDOWN` | 3600 s | Min time between alerts for same coin |
| `NEWS_DEDUP_WINDOW` | 86400 s | Time before a story can be reposted |
| `CRYPTOPANIC_FILTER` | `hot` | CryptoPanic filter (`hot` / `rising` / `important`) |

---

## Stopping the bot

```bash
launchctl unload ~/Library/LaunchAgents/com.cryptobot.plist
```

---

## File structure

```
crypto-bot/
├── bot.py              # Main entry point & scheduler
├── price_monitor.py    # CoinGecko price polling + alert logic
├── news_monitor.py     # CryptoPanic news polling + dedup
├── twitter_client.py   # Tweepy v2 wrapper
├── config.py           # All settings & env-var loading
├── requirements.txt    # Python dependencies
├── .env.example        # Credential template
├── com.cryptobot.plist # launchd service template
└── setup_mac.sh        # One-shot Mac installer
```
