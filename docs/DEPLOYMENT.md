# Deployment & Operations Guide

This document outlines deployment pipelines, docker topologies, process configurations, environment parameters, and log strategies for development and production.

---

## 1. Environment Configuration (.env)

The system utilizes python-dotenv to decouple secrets from code. Maintain separate files for environment splits:

```ini
# Core Configuration
ENV=PROD                                   # DEV or PROD
SQLITE_PATH=data/guzel_tahmin.db           # DB path

# API Config
API_HOST=0.0.0.0
API_PORT=8000

# Telegram Bot Integrations
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_FREE_CHANNEL_ID=-100xxxxxxxxxx
TELEGRAM_VIP_CHANNEL_ID=-100yyyyyyyyyy

# Quantitative Flags
MINIMUM_EDGE=0.02                          # 2% Value Edge minimum filter
DOCKER_MODE=false
```

---

## 2. Docker Compose Infrastructure

Deploy the full application stack in production using Docker Compose.

### Dockerfile (Base Runner)
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

### docker-compose.yml
```yaml
version: '3.8'

services:
  api:
    build: .
    command: uvicorn api:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    env_file:
      - .env
    restart: always

  bot:
    build: .
    command: python bot.py
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    env_file:
      - .env
    restart: always

  worker:
    build: .
    command: python worker.py
    volumes:
      - ./data:/app/data
      - ./models:/app/models
    env_file:
      - .env
    restart: always
```

---

## 3. Windows Process Management (PowerShell Watchdog)

On Windows systems (without Docker), the bot can be run as a background service supervised by the **Windows Task Scheduler** and a watchdog script.

### 1. Watchdog Script (`scripts/watchdog_bot.ps1`)
The script [watchdog_bot.ps1](file:///c:/Users/WIN/Desktop/Güzel Tahmin/scripts/watchdog_bot.ps1) dynamically resolves the project directory, checks if the bot is running, and launches it in the background if it is offline:
```powershell
# Monitors and restarts the Telegram Bot process
$ScriptDir = $PSScriptRoot
$WorkingDirectory = Split-Path -Parent $ScriptDir
$Interpreter = "$WorkingDirectory\.venv\Scripts\python.exe"
$Arguments = "-m app.telegram_bot"

$Process = Get-CimInstance Win32_Process -Filter "CommandLine like '%app.telegram_bot%'"
if ($Process -eq $null) {
    Start-Process -FilePath $Interpreter -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -RedirectStandardOutput "$WorkingDirectory\data\bot_stdout.log" -RedirectStandardError "$WorkingDirectory\data\bot_stderr.log" -WindowStyle Hidden
}
```

### 2. Task Scheduler Configuration
To register the watchdog to run every 5 minutes automatically:
```powershell
schtasks /create /tn "GuzelTahminBotWatchdog" /tr "powershell.exe -ExecutionPolicy Bypass -File 'C:\Users\WIN\Desktop\Güzel Tahmin\scripts\watchdog_bot.ps1'" /sc MINUTE /mo 5 /f
```

---

## 4. Alternative Process Management (PM2)

If deploying to a VM (Linux) without Docker, manage Python processes using PM2:

Create an `ecosystem.config.js` file:
```javascript
module.exports = {
  apps: [
    {
      name: 'guzel-tahmin-api',
      script: 'uvicorn',
      args: 'api:app --host 0.0.0.0 --port 8000',
      interpreter: 'none',
      restart_delay: 3000
    },
    {
      name: 'guzel-tahmin-bot',
      script: 'python',
      args: 'bot.py',
      restart_delay: 5000
    },
    {
      name: 'guzel-tahmin-worker',
      script: 'python',
      args: 'worker.py',
      restart_delay: 5000
    }
  ]
};
```

---

## 4. Logging & Monitoring Strategy

The system enforces a multi-tier logging strategy:

1. **Standard System Logs:** Python `logging` streams output to stdout. In production, stderr is written to log files (with `RotatingFileHandler` support, 10MB limit per file).
2. **Database Bot Logs:** Operational actions, manual rollbacks, and Level 1-3 drift alerts are written to the database `bot_activity_log` table.
3. **Health Checks:** A GET request to `/health` on the API reports database availability and active model states.
