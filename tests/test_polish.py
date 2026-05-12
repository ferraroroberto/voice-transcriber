"""Unit tests for `src/polish.py` — the local-llm-hub client."""

from __future__ import annotations

# Standard library imports
from unittest.mock import MagicMock

# Third-party imports
import pytest
import requests

# Local imports
from src.polish import (
    POLISH_SYSTEM_PROMPT,
    PolishClient,
    PolishError,
    _extract_text,
)


# ---------------------------------------------------------------------------
# _extract_text — strips <think> blocks and pulls the assistant message.
# ---------------------------------------------------------------------------

class TestExtractText:
    def test_simple_text_block(self):
        body = {"content": [{"type": "text", "text": "hello world"}]}
        assert _extract_text(body) == "hello world"

    def test_concatenates_multiple_text_blocks(self):
        body = {
            "content": [
                {"type": "text", "text": "part one. "},
                {"type": "text", "text": "part two."},
            ]
        }
        assert _extract_text(body) == "part one. part two."

    def test_ignores_non_text_blocks(self):
        body = {
            "content": [
                {"type": "image", "source": {"data": "..."}},
                {"type": "text", "text": "kept"},
            ]
        }
        assert _extract_text(body) == "kept"

    def test_strips_complete_think_block(self):
        body = {
            "content": [
                {"type": "text", "text": "<think>secret</think>visible"}
            ]
        }
        assert _extract_text(body) == "visible"

    def test_leaves_unterminated_think_intact(self):
        """An open <think> with no closing tag is preserved so the caller
        can detect the mid-reasoning truncation and surface a clearer
        error."""
        body = {
            "content": [{"type": "text", "text": "<think>chain of thought"}]
        }
        assert "<think>" in _extract_text(body)

    def test_empty_when_content_missing(self):
        assert _extract_text({}) == ""
        assert _extract_text({"content": "not a list"}) == ""


# ---------------------------------------------------------------------------
# PolishClient.is_reachable
# ---------------------------------------------------------------------------

class TestIsReachable:
    def test_returns_true_on_200(self, mocker):
        client = PolishClient(base_url="http://hub:8000")
        fake_session = mocker.patch.object(client, "_session")
        fake_session.get.return_value = MagicMock(status_code=200)
        assert client.is_reachable() is True
        fake_session.get.assert_called_once_with(
            "http://hub:8000/v1/models", timeout=2.0
        )

    def test_returns_false_on_non_200(self, mocker):
        client = PolishClient(base_url="http://hub:8000")
        fake_session = mocker.patch.object(client, "_session")
        fake_session.get.return_value = MagicMock(status_code=500)
        assert client.is_reachable() is False

    def test_returns_false_on_connection_error(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        fake_session.get.side_effect = requests.ConnectionError("nope")
        assert client.is_reachable() is False


# ---------------------------------------------------------------------------
# PolishClient.polish — happy path
# ---------------------------------------------------------------------------

class TestPolishHappyPath:
    def _ok_response(self, text: str = "polished text", stop_reason: str = "end_turn"):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "id": "msg_x",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "model": "gemini_flash",
        }
        return resp

    def test_returns_polished_text(self, mocker):
        client = PolishClient(base_url="http://hub:8000")
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.return_value = self._ok_response("clean output")
        result = client.polish("dirty input", model="gemini_flash")
        assert result.polished_text == "clean output"
        assert result.model == "gemini_flash"

    def test_wraps_text_in_transcript_tags(self, mocker):
        client = PolishClient(base_url="http://hub:8000")
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.return_value = self._ok_response()
        client.polish("the input", model="gemini_flash")
        call = fake_session.post.call_args
        payload = call.kwargs["json"]
        assert payload["messages"][0]["content"] == "<transcript>\nthe input\n</transcript>"
        assert payload["model"] == "gemini_flash"

    def test_uses_default_system_prompt_when_none_provided(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.return_value = self._ok_response()
        client.polish("x", model="gemini_flash")
        assert fake_session.post.call_args.kwargs["json"]["system"] == POLISH_SYSTEM_PROMPT

    def test_uses_custom_system_prompt_when_provided(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.return_value = self._ok_response()
        client.polish("x", model="gemini_flash", system="custom prompt")
        assert fake_session.post.call_args.kwargs["json"]["system"] == "custom prompt"

    def test_falls_back_to_default_when_custom_system_is_blank(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.return_value = self._ok_response()
        client.polish("x", model="gemini_flash", system="   ")
        assert fake_session.post.call_args.kwargs["json"]["system"] == POLISH_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# PolishClient.polish — error paths
# ---------------------------------------------------------------------------

class TestPolishErrors:
    def test_empty_input_raises(self):
        client = PolishClient()
        with pytest.raises(PolishError, match="empty"):
            client.polish("   ", model="gemini_flash")

    def test_connection_error_wraps_as_polish_error(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        fake_session.post.side_effect = requests.ConnectionError("hub down")
        with pytest.raises(PolishError, match="could not reach LLM hub"):
            client.polish("x", model="gemini_flash")

    def test_non_200_status_raises(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        resp = MagicMock(status_code=502, text="upstream error")
        fake_session.post.return_value = resp
        with pytest.raises(PolishError, match="502"):
            client.polish("x", model="gemini_flash")

    def test_non_json_body_raises(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("no json")
        fake_session.post.return_value = resp
        with pytest.raises(PolishError, match="non-JSON"):
            client.polish("x", model="gemini_flash")

    def test_unterminated_think_block_raises_with_helpful_message(self, mocker):
        """Reasoning model exhausted its token budget mid-think — the
        error should hint at fallback options."""
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "content": [{"type": "text", "text": "<think>still thinking..."}],
            "stop_reason": "max_tokens",
        }
        fake_session.post.return_value = resp
        with pytest.raises(PolishError, match="exhausted its token budget"):
            client.polish("x", model="qwen_thinking")

    def test_empty_response_raises(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"content": [], "stop_reason": "end_turn"}
        fake_session.post.return_value = resp
        with pytest.raises(PolishError, match="empty response"):
            client.polish("x", model="gemini_flash")

    def test_max_tokens_with_empty_polished_raises(self, mocker):
        client = PolishClient()
        fake_session = mocker.patch.object(client, "_session")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "content": [{"type": "text", "text": ""}],
            "stop_reason": "max_tokens",
        }
        fake_session.post.return_value = resp
        with pytest.raises(PolishError, match="exhausted"):
            client.polish("x", model="gemini_flash")


# ---------------------------------------------------------------------------
# Base URL handling
# ---------------------------------------------------------------------------

class TestBaseUrl:
    def test_strips_trailing_slash(self):
        client = PolishClient(base_url="http://hub:8000/")
        assert client.base_url == "http://hub:8000"

    def test_close_is_idempotent(self):
        client = PolishClient()
        client.close()
        # Calling close twice should not raise.
        client.close()
