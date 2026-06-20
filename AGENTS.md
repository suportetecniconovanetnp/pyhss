# Repository Guidelines

## Project Structure & Module Organization
`services/` contains the runnable daemons (`apiService.py`, `diameterService.py`, `hssService.py`, and related workers). Core logic lives in `lib/`, including Diameter handling, database access, config loading, metrics, and GSUP controllers under `lib/gsup/`. Tests are in `tests/`, with shared fixtures in `tests/conftest.py` and schema snapshots in `tests/db_schema/`. Operational assets live in `docker/`, `systemd/`, `debian/`, `docs/`, and `tools/`.

## Build, Test, and Development Commands
Create a virtual environment, then install runtime and test dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt -r requirements-test.txt
```

Run the full test suite with `pytest` or use CI-like verbosity with `pytest -xvv`. Start local services directly from `services/`, for example `python3 services/apiService.py` or `python3 services/hssService.py`. For a full development stack, use `cd docker && docker compose up --build -d`.

## Coding Style & Naming Conventions
Use Python with 4-space indentation and keep lines within the `ruff` limit of 120 characters. Match existing naming: modules and functions use `snake_case`, classes use `CamelCase`, and database-backed model constants remain uppercase when mirroring current code (`APN`, `SUBSCRIBER`). Prefer small, targeted changes over broad refactors because service modules are tightly coupled through Redis messaging and shared config.

## Testing Guidelines
Tests use `pytest`, with some `unittest.TestCase` classes already integrated. Name new files `tests/test_*.py` and keep test helpers in `tests/conftest.py` or local fixtures. The suite expects `PYHSS_CONFIG=tests/config.yaml`; `pyproject.toml` already injects that for `pytest`. Some tests require local services such as `redis-server`, MariaDB, or PostgreSQL, and slow tests should be marked with `@pytest.mark.slow`.

## Commit & Pull Request Guidelines
Recent commits use short, imperative summaries focused on behavior changes, for example `Blocks reattaching disabled subscribers...`. Follow that pattern and keep each commit scoped to one logical fix. Pull requests should describe the affected service or interface, list config or schema impacts, reference related issues, and include test evidence (`pytest`, targeted test names, or Docker verification) before review.

## Configuration & Operations
Runtime configuration is driven by `config.yaml` or the `PYHSS_CONFIG` environment variable. Do not commit secrets, production subscriber data, or environment-specific overrides. When changing ports, service startup, or packaging behavior, update the matching files in `docker/`, `systemd/`, `debian/`, and `docs/`.
