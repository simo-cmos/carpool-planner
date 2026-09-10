# Contributing

Thanks for taking a look. This is a small project — issues and pull requests are both welcome.

## Getting set up

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # .venv\Scripts\pip on Windows
.venv/bin/python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

On Windows you can instead run `start-app.cmd`, which creates the virtualenv, installs
dependencies, and starts the app plus the desktop notifier in the background.

## Before opening a pull request

Run the tests:

```bash
python -m unittest discover -s tests
```

All 127 should pass. CI runs the same command on Python 3.11 and 3.12.

## Layout

```text
app/       FastAPI routes, templates, static assets, services
core/      Routing and optimization logic, no web dependencies
scripts/   Windows helpers for the app, tunnels and notifications
tests/     Automated tests
legacy/    The original desktop prototype, kept for reference
demo/      Synthetic demo workspace
```

## Debugging

Set `DMPROJECT_DEBUG=1` for request timings, source locations on each log line, and
verbose traces through optimization, geocoding and SQL. Logs are written to `logs/`.

Set `DMPROJECT_DATA_DIR` to point at a throwaway folder when you want a clean database
without touching your real one.

## Things worth knowing

- **Never commit real personal data.** `data/` and `logs/` are gitignored and must stay that
  way. Tests use synthetic participants — please keep it that way, including coordinates.
- `core/` holds the routing and optimization logic and has no web dependencies. `app/` is the
  FastAPI layer. Keep that split.
- User-facing strings go through the `t()` helper in `app/i18n.py`. `tests/test_source_hygiene.py`
  will fail if you add an untranslated string.
- Match the surrounding style rather than introducing a new one.
