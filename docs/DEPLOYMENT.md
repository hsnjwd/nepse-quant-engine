# NEPSE Quant Engine — Deployment Guide

This document covers production deployment for the NEPSE Quant Engine using Docker, native Linux, or Windows hosts.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start (Docker)](#quick-start-docker)
3. [Environment Configuration](#environment-configuration)
4. [Native Linux / macOS Deployment](#native-linux--macos-deployment)
5. [Native Windows Deployment](#native-windows-deployment)
6. [GitHub Container Registry (GHCR)](#github-container-registry)
7. [Backup & Restore](#backup--restore)
8. [Monitoring & Health Checks](#monitoring--health-checks)
9. [Logging](#logging)
10. [Security Considerations](#security-considerations)
11. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Docker Deployment

| Dependency | Version | Purpose |
|---|---|---|
| Docker | 24.0+ | Container runtime |
| Docker Compose | 2.20+ | Multi-service orchestration |
| Git | 2.40+ | Source control |

### Native Deployment

| Dependency | Version | Purpose |
|---|---|---|
| Python | 3.12+ | Runtime |
| pip | 24.0+ | Package management |
| Git | 2.40+ | Source control |

---

## Quick Start (Docker)

The fastest way to get the full platform running:

```bash
# 1. Clone the repository
git clone https://github.com/hsnjwd/nepse-quant-engine.git
cd nepse-quant-engine

# 2. Configure environment
cp .env.example .env
# Edit .env with your settings (at minimum TELEGRAM_TOKEN if using the bot)

# 3. Start the Streamlit frontend
docker compose up -d web

# 4. Open in your browser
# http://localhost:8501
```

To start all services (frontend + API + optional bot):

```bash
docker compose up -d
```

To include the Telegram bot (requires `TELEGRAM_TOKEN` in `.env`):

```bash
docker compose --profile bot up -d
```

### Building for Production

```bash
# Build the production image
docker compose build

# Or build manually with a specific target
docker build --target web -t nepse-quant-engine:latest .
docker build --target api -t nepse-quant-engine-api:latest .
```

### Stopping

```bash
docker compose down
# To also remove persistent volumes:
docker compose down -v
```

---

## Environment Configuration

All configuration is driven by environment variables. Copy `.env.example` to `.env` and customise:

```bash
cp .env.example .env
```

### Essential Variables

| Variable | Default | Description |
|---|---|---|
| `DATA_DIRECTORY` | `data/raw` | Path to local CSV price data |
| `NEPSE_SCRAPER_URL` | `https://nepseapi.surajrimal.dev/api/v1` | Community NEPSE API |
| `NEPSE_CLIENT_URL` | `https://shubhamnpk.github.io/yonepse/data` | GitHub-hosted dataset |

### Caching

| Variable | Default | Description |
|---|---|---|
| `CACHE_MEMORY_TTL` | `60` | In-memory cache TTL (seconds) |
| `CACHE_DISK_TTL` | `600` | On-disk cache TTL (seconds) |
| `CACHE_REFRESH_INTERVAL` | `60` | Background cache refresh interval |

### Performance Monitoring

| Variable | Default | Description |
|---|---|---|
| `ENABLE_PERFORMANCE_MONITORING` | `false` | Enable request metrics collection |
| `RATE_LIMIT` | `10` | Max API requests per second |

---

## Native Linux / macOS Deployment

### 1. Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Run Services

**Streamlit Frontend:**
```bash
./launcher/run_app.sh
# Or directly:
python -m streamlit run app.py --server.port 8501
```

**FastAPI Backend:**
```bash
./launcher/run_app.sh --api
# Or directly:
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

**All Services (via tmux):**
```bash
./launcher/run_app.sh --all
```

### 4. Set up as a Systemd Service

Create `/etc/systemd/system/nepse-web.service`:

```ini
[Unit]
Description=NEPSE Quant Engine — Streamlit Frontend
After=network.target

[Service]
Type=simple
User=nepse
WorkingDirectory=/opt/nepse-quant-engine
EnvironmentFile=/opt/nepse-quant-engine/.env
ExecStart=/opt/nepse-quant-engine/.venv/bin/python -m streamlit run app.py --server.port 8501
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable nepse-web
sudo systemctl start nepse-web
sudo systemctl status nepse-web
```

---

## Native Windows Deployment

### 1. Install Dependencies

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```cmd
copy .env.example .env
REM Edit .env with your settings
```

### 3. Run Services

**Streamlit Frontend:**
```cmd
launcher\run_app.bat
```

**FastAPI Backend:**
```cmd
launcher\start_engine.bat
```

---

## Backup & Restore

### Backup

**Linux / macOS:**
```bash
# Default backup (creates timestamped archive in ./backups/)
./scripts/backup.sh

# Custom destination
./scripts/backup.sh /mnt/nas/nepse_backups
```

**Windows:**
```cmd
scripts\backup.bat
scripts\backup.bat D:\backups
```

The backup script archives:
- Market data CSV files (`data/`)
- Environment configuration (`.env`)
- User cache and settings (`~/.nepse/`)
- Application logs (`logs/`)

### Restore

**Linux / macOS:**
```bash
# Extract the backup
tar -xzf backups/nepse_quant_engine_backup_20240730_120000.tar.gz

# Restore data
cp -r nepse_quant_engine_backup_20240730_120000/data ./
cp -r nepse_quant_engine_backup_20240730_120000/nepse_home ~/.nepse
cp nepse_quant_engine_backup_20240730_120000/.env ./.env
```

**Windows:**
```powershell
# Extract the backup
Expand-Archive -Path "backups\nepse_quant_engine_backup_20240730_120000.zip" -DestinationPath ".\restore_temp"

# Restore data
Copy-Item -Recurse ".\restore_temp\data" ".\"
Copy-Item -Recurse ".\restore_temp\nepse_home" "$env:USERPROFILE\.nepse"
Copy-Item ".\restore_temp\.env" ".\"
```

---

## Monitoring & Health Checks

### Docker Health Checks

Both the `web` and `api` services have built-in health checks:

```bash
# Check container health
docker inspect nepse-web --format='{{.State.Health.Status}}'
docker inspect nepse-api --format='{{.State.Health.Status}}'
```

### Manual Health Checks

```bash
# Streamlit health endpoint
curl http://localhost:8501/_stcore/health

# FastAPI health endpoint
curl http://localhost:8000/

# Get engine status
curl http://localhost:8000/
```

### Viewing Logs

```bash
# Docker
docker compose logs -f web
docker compose logs -f api

# Native (Linux)
journalctl -u nepse-web -f
tail -f logs/engine.log
```

---

## Logging

Logs are written to `logs/engine.log` with the format:

```
2024-07-30 12:00:00,000 | INFO | src.data.service | Background refresh started
```

### Log Levels

| Level | When to use |
|---|---|
| `INFO` | Normal operations, cache refreshes, provider switches |
| `WARNING` | API failures, provider fallbacks, retries |
| `ERROR` | Unrecoverable errors, data corruption |

### Production Logging Configuration

For production, configure the log level via environment:

```bash
export LOG_LEVEL=WARNING
```

Or configure your logging framework to use JSON-formatted logs for ingestion into tools like Loki, Datadog, or ELK.

---

## Security Considerations

1. **`.env` file**: Contains sensitive tokens (Telegram). Never commit it to Git. The `.gitignore` already excludes `.env`.

2. **Network exposure**: By default, Streamlit binds to `0.0.0.0:8501`. In production, use a reverse proxy (Nginx, Caddy, Traefik) with TLS termination.

3. **CORS**: The Docker Compose setup disables CORS for the API. For production, configure `--server.enableCORS=true` and set allowed origins explicitly.

4. **Non-root user**: The Docker containers run as the `nepse` user (non-root). All data directories use Docker volumes.

5. **Dependency scanning**: Regularly scan dependencies:
   ```bash
   pip audit
   ```

6. **Secrets management**: For production, use Docker secrets or a secrets manager instead of plain `.env` files:
   ```yaml
   secrets:
     telegram_token:
       file: ./secrets/telegram_token.txt
   ```

---

## Troubleshooting

| Symptom | Likely Cause | Solution |
|---|---|---|
| `ModuleNotFoundError: No module named 'src'` | PYTHONPATH not set | Run from project root: `cd nepse-quant-engine && python -m streamlit run app.py` |
| API returns 404 | Wrong API URL | Check `API_BASE_URL` in `.env` |
| Docker build fails | Missing BuildKit | Set `DOCKER_BUILDKIT=1` |
| Streamlit crashes on startup | Port in use | Change port: `STREAMLIT_PORT=8502` |
| Telegram bot not responding | Missing token | Set `TELEGRAM_TOKEN` in `.env` |
| Cache debug page empty | Monitoring disabled | Set `ENABLE_PERFORMANCE_MONITORING=true` |
| `docker compose` not found | Docker Compose v1 vs v2 | Use `docker-compose` (v1) or install Docker Compose v2 plugin |

---

## Architecture Diagram

```
                          ┌──────────────────────┐
                          │    Reverse Proxy      │
                          │   (Nginx / Traefik)   │
                          └──────┬───────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
            ┌───────▼───────┐        ┌───────▼───────┐
            │  Streamlit    │        │   FastAPI     │
            │  :8501        │        │   :8000       │
            └───────┬───────┘        └───────┬───────┘
                    │                         │
                    └────────────┬────────────┘
                                 │
                        ┌───────▼───────┐
                        │  DataService  │
                        │  (Singleton)  │
                        └───────┬───────┘
                                │
                    ┌───────────┼───────────┐
                    │           │           │
              ┌─────▼───┐ ┌────▼────┐ ┌────▼────┐
              │  API    │ │  CSV   │ │  Cache  │
              │Provider │ │Provider│ │(Tiered) │
              └─────────┘ └─────────┘ └─────────┘
```

---

## Deployment Checklist

- [ ] Environment variables configured (`.env`)
- [ ] Data directory populated with CSV files
- [ ] Docker / system dependencies installed
- [ ] Docker Compose or systemd configured
- [ ] Health checks operational
- [ ] Backup script scheduled (cron / Task Scheduler)
- [ ] Reverse proxy configured (TLS termination)
- [ ] Firewall rules applied (ports 8501, 8000)
- [ ] Logs shipping configured (optional)
- [ ] Monitoring alerts set up (optional)

---

## CI/CD Pipeline

The project includes two GitHub Actions workflows:

1. **`ci.yml`** — Runs on every push/PR: installs deps, compiles, runs tests
2. **`release.yml`** — Runs on tag push: builds Docker image, pushes to GHCR, creates GitHub Release

To trigger a release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This will:
1. Run the full test suite
2. Build the Docker image
3. Push to `ghcr.io/hsnjwd/nepse-quant-engine`
4. Create a GitHub Release with auto-generated release notes
