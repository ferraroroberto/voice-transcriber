# 2026-05-12 — Polish-model refactor + full test suite

Two changes shipped in this batch:

1. The polish-model dropdown now uses the local-llm-hub's stable
   aliases (`claude_haiku`, `claude_sonnet`, `claude_opus`,
   `gemini_lite`, `gemini_flash`, `gemini_pro`), with `gemini_flash` as
   the new default. The list lives in JSON, not Python — so adding or
   renaming an alias is a one-line edit with no repo push.
2. The repo gained its first test suite — pytest + httpx for Python,
   Vitest for the static JS (optional), plus an end-to-end smoke test
   that boots a real `uvicorn` process.

## Why

Hub-side: `local-llm-hub` now exposes Claude (Haiku / Sonnet / Opus)
and Gemini (Lite / Flash / Pro) under stable version-free aliases. The
old `agentic_light` / `agentic_heavy` local roles were dropped from
the default dropdown — they still work if you wire them back in, but
they're no longer the daily-driver path.

Refactor side: the previous version of `src/webapp_config.py` carried
the model list as Python literals (`DEFAULT_POLISH_MODEL = "agentic_light"`,
`DEFAULT_POLISH_MODELS = (…)`). Every time the hub gained a model,
this repo needed a commit. Now the list comes from the committed
`config/webapp_config.sample.json`; Python loads it at first-run and
the runtime `webapp_config.json` (gitignored) layers user overrides on
top.

Test-suite side: the repo was test-free up to this point. A simple
filler-word polish bug shipped earlier in the week because nothing
caught the validation gap server-side. Time to fix that.

## What changed

### `src/webapp_config.py`

- Removed `DEFAULT_POLISH_MODEL` and `DEFAULT_POLISH_MODELS` constants.
- Added `_sample_polish_defaults()` which reads
  `config/webapp_config.sample.json` and returns
  `(default_alias, available_aliases)`.
- `WebappConfig` defaults now `default_factory` off that function, so
  every fresh instance pulls from JSON. Tests cover the "sample is
  missing / corrupt" fallback too.

### `config/webapp_config.sample.json`

```json
{
  "polish_model_default": "gemini_flash",
  "polish_models_available": [
    "claude_haiku", "claude_sonnet", "claude_opus",
    "gemini_lite",  "gemini_flash",  "gemini_pro"
  ]
}
```

### `app/webapp/static/app.js`

- `polishModelLabel(id)` now derives friendly labels by title-casing
  underscore-separated segments. No hardcoded map.
- `applyConfigDefaults`'s offline fallback has an empty
  `polish_models_available` array (was a hardcoded list). The real
  list always comes from `/api/config`.

### `src/polish.py`

- Docstring + the token-budget error message updated for the new alias
  scheme.

### Tests — new files

| Area | Count |
|------|-------|
| Test infrastructure (`requirements-dev.txt`, `pytest.ini`, `tests/__init__.py`, `tests/conftest.py`) | 4 |
| `src/` unit tests (`test_webapp_config`, `test_polish`, `test_polish_prompts`, `test_app_config`, `test_silence`, `test_archive`, `test_vocabulary`, `test_snippets`, `test_transcription_client`) | 9 |
| FastAPI route tests (`test_webapp_api_basics`, `test_webapp_api_polish`, `test_webapp_api_sessions`, `test_webapp_api_auth`) | 4 |
| JS bridge + Vitest harness (`tests/test_static_app_js.py`, `app/webapp/static/__tests__/polishModelLabel.test.js`, `package.json`) | 3 |
| Smoke test (`tests/test_webapp_smoke.py`) | 1 |

**263 tests total** — 262 pass on a Python-only machine, 1 Vitest
runner skips when Node.js isn't on `PATH`. Smoke marker keeps the
fast iteration loop at ~2 s (`pytest -m "not smoke"`).

### Notable design points

- **No model literals in Python.** A dedicated test
  (`TestNoModelLiteralsInPython::test_python_module_does_not_hardcode_model_names`)
  greps `src/webapp_config.py` for the alias strings and fails if any
  reappear. Catches regressions where someone "helpfully" re-adds a
  default constant.
- **Source-of-truth contract.** Both Python and JS read the same JSON
  file (Python directly, JS via `/api/config`). Adding `gemini_4_flash`
  is one line; the title-case rule handles the label.
- **Sample-driven assertions.** Tests don't hardcode the six aliases —
  they read them from the `sample_polish_payload` fixture
  (`tests/conftest.py`). When the canonical list evolves, only
  `webapp_config.sample.json` and the soft pin in
  `test_sample_lists_six_aliases_with_gemini_flash_default` need
  updating.
- **JS parity port.** Node.js isn't on every dev box, so the JS
  function is mirrored in Python (`polish_model_label_py`) and
  exercised against the same expected outputs the Vitest suite
  asserts. Plus regex pins on the JS source so refactors can't
  silently break the contract.
- **TestClient + auth gate.** The FastAPI `TestClient` reports as
  `testclient`, not `127.0.0.1`, so it doesn't hit the loopback bypass.
  `tests/conftest.py` clears `auth_token`/`auth_password` on the test
  app and `tests/test_webapp_api_auth.py` ships a tiny ASGI wrapper
  that rewrites `scope["client"]` to either `127.0.0.1` or
  `10.0.0.42` so both code paths get exercised.

## Validation

```bat
.venv\Scripts\python.exe -m pytest
:: 262 passed, 1 skipped in ~7 s

.venv\Scripts\python.exe -m pytest -m "not smoke"
:: 258 passed, 1 skipped, 4 deselected in ~2 s
```

End-to-end manual check:

1. Webapp restarted with the new config.
2. `GET https://127.0.0.1:8443/api/config` returns the six new aliases
   with `gemini_flash` as the default.
3. `POST /api/polish-text` with `{"text": "Um, …", "model": "gemini_flash"}`
   returns a cleaned transcript through the hub's `gemini` CLI path.

## Files touched

```
M  README.md
M  app/webapp/static/app.js
M  config/webapp_config.sample.json
M  src/polish.py
M  src/webapp_config.py
A  requirements-dev.txt
A  pytest.ini
A  package.json
A  tests/__init__.py
A  tests/conftest.py
A  tests/test_app_config.py
A  tests/test_archive.py
A  tests/test_polish.py
A  tests/test_polish_prompts.py
A  tests/test_silence.py
A  tests/test_snippets.py
A  tests/test_static_app_js.py
A  tests/test_transcription_client.py
A  tests/test_vocabulary.py
A  tests/test_webapp_api_auth.py
A  tests/test_webapp_api_basics.py
A  tests/test_webapp_api_polish.py
A  tests/test_webapp_api_sessions.py
A  tests/test_webapp_config.py
A  tests/test_webapp_smoke.py
A  app/webapp/static/__tests__/polishModelLabel.test.js
A  docs/2026-05-12-polish-models-json-and-test-suite.md
```

## Local-only (not committed)

```
M  config/webapp_config.json  (gitignored — runtime user settings)
```
