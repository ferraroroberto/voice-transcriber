"""Anti-drift guard for this repo's `.fleet.toml` `[e2e]` table.

Loads the real `.fleet.toml` and asserts representative paths land in the
tier their rule intends — an edit that silently under-routes a real e2e
surface fails here. Mechanism + fail-safe behavior are covered by
project-scaffolding's own `tests/test_classify_e2e.py`; this file only
guards this repo's declared rules.
"""

from __future__ import annotations

from pathlib import Path

from scripts.classify_e2e import load_config, classify

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_FLEET_TOML = REPO_ROOT / ".fleet.toml"


def test_real_fleet_toml_declares_usable_e2e_table() -> None:
    cfg = load_config(REAL_FLEET_TOML)
    assert cfg.source == "declared", (
        "this repo's .fleet.toml must declare a usable [e2e] table"
    )
    assert cfg.rules, "at least one [[e2e.rule]] must be declared"


def test_real_rules_route_representative_paths() -> None:
    cfg = load_config(REAL_FLEET_TOML)

    def tier(*paths: str) -> str:
        return classify(list(paths), cfg).tier

    # The webapp (routers + static css/js/html) -> full.
    assert tier("app/webapp/routers/activity.py") == "full"
    assert tier("app/webapp/static/app.js") == "full"
    assert tier("app/webapp/static/styles.css") == "full"
    assert tier("app/webapp/static/index.html") == "full"

    # The e2e suite itself + its boot conftest -> full.
    assert tier("tests/e2e/test_smoke.py") == "full"
    assert tier("tests/e2e/conftest.py") == "full"

    # Inert static assets -> static.
    assert tier("app/webapp/static/icon-192.png") == "static"
    assert tier("app/webapp/static/favicon.ico") == "static"

    # Tray/GUI, CLI, shared logic, tests, docs, markdown -> skip.
    assert tier("app/gui/service_supervisor.py") == "skip"
    assert tier("app/cli/main.py") == "skip"
    assert tier("src/app_config.py") == "skip"
    assert tier("tests/test_app_config.py") == "skip"
    assert tier("scripts/gen_token.py") == "skip"
    assert tier("docs/architecture.mmd") == "skip"
    assert tier("README.md") == "skip"

    # Mixed real diff (gui + webapp) -> full.
    assert tier("app/gui/service_supervisor.py", "app/webapp/static/app.js") == "full"
