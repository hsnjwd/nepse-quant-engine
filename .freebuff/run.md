# NEPSE Quant Engine — Run Doc

How to reproduce artifacts and run the app's servers from a fresh checkout.

## Reproducing artifacts

1. Install Python dependencies (Python 3.12+):

   ```bash
   pip install -r requirements.txt
   ```

   Optional extras used by the UI and exporters:

   ```bash
   pip install streamlit plotly reportlab
   ```

2. No secret/env files are required to run locally. Configuration lives in
   `src/config.py` (API URLs, cache TTLs, rate limits). For deployments, copy
   `.env.example` to `.env` and set values as needed — there is no default
   `.env.local` in the repo.

3. No build step (no frontend bundling; the Streamlit app and static HTML UI
   are served as-is).

## Running the servers

### Option A — Streamlit app (primary UI)

```bash
python -m streamlit run app.py
```

- Default port: **8501** (override with `--server.port`).
- This is the full trading-terminal UI (dashboard, scanner, AI, ML, backtest,
  portfolio, broker, plugins, system status).

### Option B — FastAPI backend (REST API + static web UI)

The static HTML UI (`ui/index.html`) talks to the FastAPI backend on port 8000.

```bash
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

- Default port: **8000**.
- Interactive docs: http://127.0.0.1:8000/docs
- **CORS (important for the web UI):** the API's default CORS allow-list only
  includes the Streamlit origins (`localhost:8501` / `127.0.0.1:8501`). The
  static web UI is served from a different origin, so the browser would block
  its fetch calls and the page would stay in demo mode. Start the API with
  CORS opened to let the UI load live data:

  PowerShell (primary):
  ```powershell
  $env:CORS_ORIGINS="*"; .venv\Scripts\python.exe -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
  ```

  Git Bash alternative:
  ```bash
  CORS_ORIGINS=* python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
  ```

  **Known issue — FastAPI/Starlette version conflict.** If uvicorn fails with
  `TypeError: Router.__init__() got an unexpected keyword argument
  'on_startup'`, the venv has FastAPI 0.115.0–0.115.5 paired with
  Starlette >= 0.40 (which removed the `on_startup`/`on_shutdown` kwargs).
  Upgrade FastAPI in the venv, then retry:

  ```powershell
  .venv\Scripts\python.exe -m pip install -U fastapi
  ```

### Option C — Standalone HTML preview (no server)

`ui/index_standalone.html` is a fully self-contained single file (inline CSS +
JS) with an offline demo-mode fallback, so it renders usable sample data with
no backend. Open it directly in a browser, or serve the `ui/` folder:

```bash
python -m http.server 8080 --directory ui
```

- Open http://127.0.0.1:8080/index_standalone.html
- The non-standalone `ui/index.html` requires the FastAPI backend (Option B)
  for live data and shows a "Disconnected" state otherwise.

## Troubleshooting — in-app terminal failures

If the Freebuff in-app terminal cannot run any command and prints

```
Skipping command-line "C:\Program Files\Git\bin\..\usr\bin\bash.exe" ... not found
```

the Git for Windows install is partial/broken: the launcher shim at
`C:\Program Files\Git\bin\bash.exe` exists but the real bash at
`C:\Program Files\Git\usr\bin\bash.exe` is missing. Run the bundled
repair launcher:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\fix_inapp_terminal.bat
```

It runs a diagnostic, auto-detects the real `bash.exe`, and (elevated when
needed) creates a junction at the standard Git path. Fully restart the
Freebuff desktop app afterwards. `scripts/fix_inapp_terminal.ps1` documents
the manual commands (repair Git for Windows, or junction the standard path).

## Logging

Runtime logs go to `logs/engine.log` (created by `src/logging/logger.py`) and
the Streamlit console. Notification history persists to
`~/.nepse/notifications.json`.
