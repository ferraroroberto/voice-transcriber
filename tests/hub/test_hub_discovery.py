"""Which LLM hub the app talks to when the machine's config names none.

Runs from a config file through `PolishClient`, with only the HTTP session
faked: `local-llm-hub` listens on :8000, `local-llm-hub-lite` on :8020.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest
import requests

from src.polish import PolishClient, PolishError
from src.webapp_config import SAMPLE_CONFIG_PATH, load_webapp_config, save_webapp_config

FULL = "http://127.0.0.1:8000"
LITE = "http://127.0.0.1:8020"
_MODELS = {"object": "list", "data": [{"id": "some-model"}]}


class _Machine:
    """What answers on this fake PC: base URL -> `/v1/models` body."""

    def __init__(self, listeners: Dict[str, object]) -> None:
        self.listeners = listeners
        self.asked: List[str] = []
        self.posted: List[str] = []

    def get(self, url: str, timeout: float) -> MagicMock:
        base = url.removesuffix("/v1/models")
        self.asked.append(base)
        if base not in self.listeners:
            raise requests.ConnectionError("refused")
        body = self.listeners[base]
        response = MagicMock(status_code=200)
        if isinstance(body, Exception):
            response.json.side_effect = body
        else:
            response.json.return_value = body
        return response

    def post(self, url: str, **_: object) -> MagicMock:
        self.posted.append(url)
        if url.removesuffix("/v1/messages") not in self.listeners:
            raise requests.ConnectionError("refused")
        response = MagicMock(status_code=200)
        response.json.return_value = {"content": [{"type": "text", "text": "clean"}]}
        return response


def _client(tmp_path: Path, config: Dict[str, object], machine: _Machine) -> PolishClient:
    path = tmp_path / "webapp_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return _client_from(path, machine)


def _client_from(path: Path, machine: _Machine) -> PolishClient:
    client = PolishClient(load_webapp_config(path).llm_hub_url)
    client._session = machine
    return client


def test_a_fresh_config_from_the_sample_finds_the_hub(tmp_path):
    path = tmp_path / "webapp_config.json"
    shutil.copyfile(SAMPLE_CONFIG_PATH, path)
    machine = _Machine({LITE: _MODELS})

    _client_from(path, machine).polish("dirty", model="m")

    assert machine.posted == [LITE + "/v1/messages"]


@pytest.mark.parametrize("config", [{}, {"llm_hub_url": ""}, {"llm_hub_url": None}], ids=["absent", "empty", "null"])
def test_unset_url_finds_the_lite_hub_when_8000_is_silent(tmp_path, config):
    machine = _Machine({LITE: _MODELS})
    client = _client(tmp_path, config, machine)

    client.polish("dirty", model="m")

    assert machine.asked == [FULL, LITE]
    assert machine.posted == [LITE + "/v1/messages"]
    assert client.base_url == LITE
    assert client.is_reachable() is True


def test_unset_url_prefers_8000_when_both_answer(tmp_path):
    machine = _Machine({FULL: _MODELS, LITE: _MODELS})
    client = _client(tmp_path, {}, machine)

    assert client.is_reachable() is True
    assert client.base_url == FULL


@pytest.mark.parametrize(
    "body", [ValueError("not JSON"), {"status": "ok"}, ["a"]], ids=["html", "no-model-list", "not-an-object"]
)
def test_a_listener_that_is_not_a_hub_is_passed_over(tmp_path, body):
    machine = _Machine({FULL: body, LITE: _MODELS})
    client = _client(tmp_path, {}, machine)

    client.polish("dirty", model="m")

    assert machine.posted == [LITE + "/v1/messages"]


def test_a_configured_url_wins_even_when_silent(tmp_path):
    machine = _Machine({LITE: _MODELS})
    client = _client(tmp_path, {"llm_hub_url": FULL}, machine)

    assert client.is_reachable() is False
    with pytest.raises(PolishError, match=":8000"):
        client.polish("dirty", model="m")
    assert LITE not in machine.asked
    assert machine.posted == [FULL + "/v1/messages"]


def test_no_hub_at_all_sends_the_transcript_nowhere(tmp_path):
    machine = _Machine({})
    client = _client(tmp_path, {}, machine)

    with pytest.raises(PolishError, match="no LLM hub answers"):
        client.polish("dirty", model="m")
    assert machine.posted == []

    # A hub that starts later is picked up without a restart.
    machine.listeners[LITE] = _MODELS
    assert client.polish("dirty", model="m").polished_text == "clean"


def test_a_found_hub_that_goes_away_is_looked_for_again(tmp_path):
    machine = _Machine({LITE: _MODELS})
    client = _client(tmp_path, {}, machine)
    client.polish("dirty", model="m")

    machine.listeners = {FULL: _MODELS}
    with pytest.raises(PolishError):
        client.polish("dirty", model="m")
    client.polish("dirty", model="m")

    assert machine.posted[-1] == FULL + "/v1/messages"


def test_saving_other_settings_does_not_pin_the_hub(tmp_path):
    path = tmp_path / "webapp_config.json"
    path.write_text("{}", encoding="utf-8")

    save_webapp_config(load_webapp_config(path), path)

    assert load_webapp_config(path).llm_hub_url == ""
