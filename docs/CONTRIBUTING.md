# Contributing

## Development Layout

Production Python code lives under `src/`. Tests live under `tests/`. Operational scripts belong in `scripts/`, service launchers in `launcher/`, and maintained documentation in `docs/`.

Read [REPOSITORY_STRUCTURE.md](REPOSITORY_STRUCTURE.md) before introducing a new top-level directory or package.

## Before Opening a Pull Request

```powershell
python -m pytest tests/ -q --tb=short
```

For changes affecting performance or deployment, also run the relevant benchmark/production gate documented in `docs/OPERATIONS.md` and `docs/PRODUCTION_READINESS.md`.

## Architecture Rules

- Use the canonical `DataService` for market data.
- Use the canonical analysis/intelligence pipeline for analysis and signals.
- Keep FastAPI routers thin.
- Do not introduce duplicate implementations under similarly named packages.
- Do not commit runtime state, credentials, logs, caches or benchmark output.
- Add regression coverage for behavioral changes.

## Pull Requests

Keep PRs focused and describe:

1. What changed.
2. Why the change was necessary.
3. Tests/gates run and their results.
4. Any migration or compatibility considerations.

Avoid drive-by rewrites of unrelated modules.
