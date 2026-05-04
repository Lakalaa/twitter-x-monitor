# Twitter/X Monitor — Web Dashboard + Telegram Bot

## Overview
A Python Flask web dashboard with a Telegram bot integration for monitoring Twitter/X accounts. Tracks followers, following lists, and complaint tweets. Sends notifications to a Telegram group.

## Tech Stack
- **Backend**: Python 3.10, Flask 3.1.3
- **Task Scheduling**: `schedule` (background thread)
- **Telegram**: `python-telegram-bot==22.7` (polling mode)
- **Twitter Scraping**: `Scweet==5.3` (lazy-imported, optional)
- **Server**: `gunicorn` (production), Flask dev server (development)
- **Deployment**: Render (https://twitter-x-monitor.onrender.com)

## Project Structure
- `app.py` — Main Flask app: API routes, scheduler, Telegram bot polling (all in one)
- `templates/index.html` — Full dashboard UI (4 tabs: Dashboard, Targets, Settings, Bot Commands)
- `tools/telegram_bot.py` — Telegram send functions (followers, following, complaints)
- `tools/targets.json` — Config: Twitter auth token, target accounts, check interval
- `requirements.txt` — Python dependencies (no C-extension packages for Render compat)
- `Procfile` — Render/gunicorn start command
- `render.yaml` — Render service config
- `.env` — Local secrets (gitignored)

## Render Deployment
- **Service ID**: `srv-d7s2rud7vvec738tlff0`
- **URL**: https://twitter-x-monitor.onrender.com
- **GitHub Repo**: https://github.com/Lakalaa/twitter-x-monitor (public)
- **Build command**: `pip install -r requirements.txt`
- **Start command**: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`
- **Env vars on Render**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TWITTER_AUTH_TOKEN`

## Environment Variables / Secrets (Replit)
- `TELEGRAM_BOT_TOKEN` — Bot token from @BotFather
- `TELEGRAM_CHAT_ID` — Target Telegram group chat ID (-1003707579699)
- `TWITTER_AUTH_TOKEN` — Twitter auth_token cookie value
- `RENDER_API_KEY` — Render API key for deployments
- `GITHUB_TOKEN` — GitHub personal access token
- `GITHUB_USERNAME` — GitHub username (lakalaa)

## Telegram Bot Commands
- `/start` — Welcome message
- `/check` — Trigger an immediate account check
- `/followers <username>` — Fetch follower list
- `/following <username>` — Fetch following list
- `/complaints <query>` — Search complaint tweets
- `/status` — Bot and scheduler status
- `/help` — Command list

## Key Architecture Notes
- Background scheduler runs in a daemon thread on app startup
- Telegram bot polling runs in a second daemon thread
- Both are gunicorn-safe: started once via `_before_request` hook with a threading.Event guard
- `RENDER=true` env var (auto-set by Render) stops bot polling on Replit when Render is live
- Scweet is lazy-imported; if not installed, scraping shows a warning instead of crashing
- `/health` endpoint returns `{"status":"ok","running":true}` for Render health checks

## Important Notes
- `twscrape` was removed from requirements.txt because it pulls in `orjson` (Rust C extension) which fails to compile on Render
- `curl_cffi`, `uvloop`, `orjson`, `lxml` are excluded for the same reason
- Scweet and tweeterpy still work locally (they're installed in Replit's `.pythonlibs`)
- The GitHub repo is public so Render can clone it without OAuth credentials
