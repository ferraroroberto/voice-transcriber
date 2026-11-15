"""Send to task-os: the payload, the idempotency key and the outcome mapping."""

from __future__ import annotations

# Standard library imports
from typing import Any, List

# Third-party imports
import pytest
import requests

from src import task_os
from src.task_os import TaskOsError, build_payload, build_title, send_to_task_os


class _Resp:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Calls(list):
    """The POSTs made, plus the responses queued for the next ones."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: List[Any] = []


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> _Calls:
    """Record every POST and answer with the queued response (201 by default)."""
    seen = _Calls()

    def fake(method: str, url: str, *, timeout: float, **kwargs: Any) -> _Resp:
        seen.append({"method": method, "url": url, "timeout": timeout, **kwargs})
        answer = seen.answers.pop(0) if seen.answers else _Resp(201, {"id": 7})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(task_os, "pooled_request", fake)
    return seen


class TestPayload:
    def test_short_take_is_title_only(self):
        payload = build_payload("  Buy milk  ", "k")
        assert payload == {"title": "Buy milk", "external_id": "k", "actor": "voice-transcriber"}

    def test_multi_line_take_keeps_full_text_in_description(self):
        payload = build_payload("Call the bank\nabout the card", "k")
        assert payload["title"] == "Call the bank"
        assert payload["description"] == "Call the bank\nabout the card"

    def test_long_first_line_is_capped(self):
        title = build_title("word " * 60)
        assert len(title) == task_os.TITLE_MAX
        assert title.endswith("…")

    def test_text_key_is_stable_and_whitespace_blind(self):
        assert task_os.text_external_id("a b ") == task_os.text_external_id(" a b")
        assert task_os.text_external_id("a") != task_os.text_external_id("b")


class TestSend:
    def test_created(self, calls):
        result = send_to_task_os("https://127.0.0.1:8448/", "Buy milk", "k1")
        assert (result.outcome, result.task_id) == ("created", 7)
        assert calls[0]["url"] == "https://127.0.0.1:8448/api/tasks"
        assert calls[0]["json"]["external_id"] == "k1"

    def test_replay_reports_existing(self, calls):
        calls.answers.append(_Resp(200, {"id": 7}))
        assert send_to_task_os("https://127.0.0.1:8448", "Buy milk", "k1").outcome == "existing"

    def test_loopback_skips_cert_check_other_hosts_verify(self, calls):
        send_to_task_os("https://127.0.0.1:8448", "x", "k")
        send_to_task_os("https://box.example.ts.net:8448", "x", "k")
        assert [c["verify"] for c in calls] == [False, True]

    def test_empty_text_is_refused_without_a_call(self, calls):
        with pytest.raises(TaskOsError) as exc:
            send_to_task_os("https://127.0.0.1:8448", "   ", "k")
        assert exc.value.status == 400
        assert calls == []

    @pytest.mark.parametrize(
        "answer, status, words",
        [
            (requests.ConnectionError("refused"), 503, "not reachable"),
            (requests.ReadTimeout("slow"), 503, "did not answer"),
            (_Resp(410, {"error": "capture_dismissed"}), 410, "deleted"),
            (_Resp(500, {"error": "boom"}), 502, "HTTP 500"),
        ],
    )
    def test_failures_keep_distinct_status_and_message(self, calls, answer, status, words):
        calls.answers.append(answer)
        with pytest.raises(TaskOsError) as exc:
            send_to_task_os("https://127.0.0.1:8448", "x", "k")
        assert exc.value.status == status
        assert words in str(exc.value)
