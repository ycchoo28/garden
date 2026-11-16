"""Model upstream checks, reasons and hints, and the interactive setup, against a fake upstream."""

import argparse
import importlib.util
import json
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "install_manifest", Path(__file__).resolve().parents[1] / "install_manifest.py"
)
assert MANIFEST_SPEC and MANIFEST_SPEC.loader
MANIFEST = importlib.util.module_from_spec(MANIFEST_SPEC)
sys.modules["install_manifest"] = MANIFEST
MANIFEST_SPEC.loader.exec_module(MANIFEST)

SPEC = importlib.util.spec_from_file_location(
    "demo_model", Path(__file__).resolve().parents[1] / "demo.py"
)
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEMO
SPEC.loader.exec_module(DEMO)

KEY = "canary-redaction-key-not-real"
GOOD_IDS = ["openai/gpt-4o-mini", "thinkingmachines/inkling-2-small:free"]
MODELS_BODY = {"object": "list", "data": [{"id": name} for name in GOOD_IDS]}
CHAT_OK = {"choices": [{"index": 0, "message": {"role": "assistant", "content": ""}}]}


class _Upstream(BaseHTTPRequestHandler):
    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        auth = self.headers.get("Authorization")
        self.server.seen.append(("GET", self.path, auth, None))
        status, body = self.server.models_reply(auth)
        self._send(status, body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        self.server.seen.append(("POST", self.path, self.headers.get("Authorization"), request))
        status, body = (
            self.server.chat_replies.popleft()
            if self.server.chat_replies
            else (
                200,
                CHAT_OK,
            )
        )
        self._send(status, body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def upstream() -> Iterator[Any]:
    """A local OpenAI-compatible upstream. Tests script /models and chat replies."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    server.seen = []
    server.chat_replies = deque()
    server.models_reply = lambda auth: (200, MODELS_BODY)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base = f"http://127.0.0.1:{server.server_port}/api/v1"
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _env(url: str, key: str = KEY, models: str = "openai/gpt-4o-mini", default: str = "") -> dict:
    return {
        DEMO.MODEL_KEYS[0]: url,
        DEMO.MODEL_KEYS[1]: key,
        DEMO.MODEL_KEYS[2]: models,
        DEMO.MODEL_KEYS[3]: default or models.split(",")[0],
    }


def _scripted(answers: list[str]) -> tuple[Callable[..., str], list[str], list[str]]:
    queue = list(answers)
    asked: list[str] = []

    def ask(prompt: str = "") -> str:
        asked.append(prompt)
        assert queue, f"unexpected prompt: {prompt!r}"
        return queue.pop(0)

    return ask, asked, queue


# --- Reasons and hints -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "message", "listing", "step", "hint_part"),
    [
        (401, "Invalid API key", False, "key", "rejected"),
        (402, "Insufficient credits", False, "model", "credits"),
        (403, "Forbidden", False, "model", "may not use"),
        (
            403,
            "thinkingmachines/inkling-2-small:free is only available on agentic harnesses.",
            False,
            "model",
            "agentic harnesses",
        ),
        (
            404,
            "No endpoints found matching your data policy (Free model training).",
            False,
            "model",
            DEMO.OPENROUTER_PRIVACY_URL,
        ),
        (404, "not found", True, "url", "OpenAI-compatible"),
        (404, "model not found", False, "model", "model id"),
        (429, "Rate limit exceeded", False, "model", "non-free"),
        (429, "Rate limit exceeded", True, "retry", "Wait"),
        (400, "max_tokens too small", False, "model", "Pick another model"),
        (503, "overloaded", False, "retry", "failing"),
    ],
)
def test_hint_maps_status_to_the_step_to_retry(
    status: int, message: str, listing: bool, step: str, hint_part: str
) -> None:
    got_step, hint = DEMO.hint_for(status, message, listing=listing)

    assert got_step == step
    assert hint_part in hint


def test_chat_check_sends_small_max_tokens_and_accepts_an_empty_reply(upstream: Any) -> None:
    upstream.chat_replies.append(
        (200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
    )

    problem = DEMO.check_chat(upstream.base, KEY, "openai/gpt-4o-mini")

    assert problem is None
    ((_method, path, _auth, body),) = [row for row in upstream.seen if row[0] == "POST"]
    assert path == "/api/v1/chat/completions"
    assert body["max_tokens"] == DEMO.CHECK_MAX_TOKENS == 16


def test_rate_limit_reason_names_the_status_and_never_shows_the_key(upstream: Any) -> None:
    upstream.chat_replies.append(
        (429, {"error": {"message": f"Rate limited for {KEY} on free models", "code": 429}})
    )

    problem = DEMO.check_chat(upstream.base, KEY, "thinkingmachines/inkling-2-small:free")

    assert problem is not None
    assert problem.step == "model"
    assert problem.reason.startswith("HTTP 429: Rate limited for [key hidden]")
    assert "non-free" in problem.hint
    assert KEY not in problem.text()


def test_data_policy_404_points_to_the_openrouter_privacy_settings(upstream: Any) -> None:
    upstream.chat_replies.append(
        (
            404,
            {
                "error": {
                    "message": "No endpoints found matching your data policy "
                    "(Free model training).",
                    "code": 404,
                }
            },
        )
    )

    problem = DEMO.check_chat(upstream.base, KEY, "thinkingmachines/inkling-2-small:free")

    assert problem is not None
    assert problem.step == "model"
    assert "No endpoints found matching your data policy" in problem.reason
    assert DEMO.OPENROUTER_PRIVACY_URL in problem.hint


def test_empty_choices_are_a_failure_with_a_reason(upstream: Any) -> None:
    upstream.chat_replies.append((200, {"choices": []}))

    problem = DEMO.check_chat(upstream.base, KEY, "openai/gpt-4o-mini")

    assert problem is not None
    assert problem.step == "model"
    assert "without any choices" in problem.reason


def test_unknown_model_is_flagged_with_close_matches(upstream: Any) -> None:
    env = _env(upstream.base, models="thinkingmachines/inkling-2-smal:free")

    problem = DEMO.check_settings(env)

    assert problem is not None
    assert problem.step == "model"
    assert "thinkingmachines/inkling-2-smal:free" in problem.reason
    assert "thinkingmachines/inkling-2-small:free" in problem.suggestions


def test_listing_failure_with_bad_key_points_back_to_the_key(upstream: Any) -> None:
    upstream.models_reply = lambda auth: (
        401,
        {"error": {"message": f"Bad key {KEY}", "code": 401}},
    )

    problem = DEMO.check_settings(_env(upstream.base))

    assert problem is not None
    assert problem.step == "key"
    assert KEY not in problem.text()


def test_unreachable_url_points_back_to_the_url() -> None:
    problem = DEMO.check_settings(_env("http://127.0.0.1:9/api/v1"))

    assert problem is not None
    assert problem.step == "url"
    assert "could not reach the upstream" in problem.reason


def test_non_interactive_setup_reports_the_reason(tmp_path: Path, capsys) -> None:
    demo = object.__new__(DEMO.Demo)
    demo.args = argparse.Namespace(fake_model=False, non_interactive=True)
    demo.env = _env("http://127.0.0.1:9/api/v1", key="")
    demo.env_file = tmp_path / ".env"

    with pytest.raises(RuntimeError, match="no key is saved"):
        demo.configure_model()


# --- Picker ----------------------------------------------------------------------------------

BIG_LIST = [f"vendor/model-{index:03d}" for index in range(40)] + GOOD_IDS


def test_picker_filters_then_picks_by_number_and_default_by_number() -> None:
    ask, asked, _ = _scripted(["inkling", "1", "1"])
    lines: list[str] = []

    chosen, default = DEMO.choose_models(BIG_LIST, ask, lines.append)

    assert chosen == [GOOD_IDS[1]]
    assert default == GOOD_IDS[1]
    assert any("inkling-2-small:free" in line for line in lines)
    assert "Default model" in asked[-1]


def test_picker_takes_a_typed_exact_id_and_multiple_numbers() -> None:
    ask, _asked, _ = _scripted(["openai/gpt-4o-mini,thinkingmachines/inkling-2-small:free", ""])

    chosen, default = DEMO.choose_models(GOOD_IDS, ask, lambda _line: None)

    assert chosen == GOOD_IDS
    assert default == GOOD_IDS[0]


def test_picker_flags_an_unknown_id_with_suggestions_and_keeps_asking() -> None:
    ask, _asked, _ = _scripted(["", "openai/gpt-4o-mni,1", "1", ""])
    lines: list[str] = []

    chosen, _default = DEMO.choose_models(GOOD_IDS, ask, lines.append)

    assert chosen == [GOOD_IDS[0]]
    flagged = [line for line in lines if line.startswith("Not in the list")]
    assert flagged and "openai/gpt-4o-mini" in flagged[0]


def test_picker_does_not_dump_the_whole_list() -> None:
    ask, _asked, _ = _scripted(["vendor", "1", ""])
    lines: list[str] = []

    DEMO.choose_models(BIG_LIST, ask, lines.append)

    before_pick = lines[: lines.index("Allowed models:")]
    listed = [line for line in before_pick if line.strip()[:1].isdigit() and "vendor/model" in line]
    assert len(listed) == DEMO.MODEL_PAGE
    assert any("40 models match" in line for line in lines)


def test_read_selection_resolves_numbers_and_ids_and_suggests_for_unknown() -> None:
    chosen, unknown = DEMO.read_selection(
        ["2", "openai/gpt-4o-mini", "openai/gpt-4o-mni", "9"], GOOD_IDS, GOOD_IDS
    )

    assert chosen == [GOOD_IDS[1], GOOD_IDS[0]]
    assert unknown["openai/gpt-4o-mni"] == ["openai/gpt-4o-mini"]
    assert unknown["9"] == []


# --- Retry only the failed step --------------------------------------------------------------


def test_failed_check_retries_only_the_failed_step(upstream: Any) -> None:
    # The first key is rejected by /models; the second key lists models.
    upstream.models_reply = lambda auth: (
        (200, MODELS_BODY) if auth == f"Bearer {'good-key-1234567'}" else (401, {"error": "bad"})
    )
    # The first chat check is rate limited, the second answers.
    upstream.chat_replies.extend(
        [
            (429, {"error": {"message": "Rate limit exceeded", "code": 429}}),
            (200, CHAT_OK),
        ]
    )
    ask, asked, leftover = _scripted(
        [
            "3",  # Other OpenAI-compatible URL
            upstream.base,
            "",  # list the first models
            "1",  # first model
            "",  # default
            "",  # after the 429: list again
            "2",  # pick the second model
            "",  # default
            "",  # save
        ]
    )
    secrets = deque(["bad-key-1234567", "good-key-1234567"])
    say_lines: list[str] = []

    result = DEMO.interactive_model_setup(
        ask=ask,
        secret=lambda prompt: secrets.popleft(),
        say=say_lines.append,
    )

    assert leftover == []
    assert result is not None
    assert result[DEMO.MODEL_KEYS[0]] == upstream.base
    assert result[DEMO.MODEL_KEYS[1]] == "good-key-1234567"
    assert result[DEMO.MODEL_KEYS[2]] == GOOD_IDS[1]
    assert result[DEMO.MODEL_KEYS[3]] == GOOD_IDS[1]
    # The URL was asked once, the 429 sent the person back to model choice only.
    assert sum("base URL" in prompt for prompt in asked) == 1
    # Two picker rounds (list, then pick) for each of the two model choices.
    assert sum("Filter, numbers" in prompt for prompt in asked) == 4
    assert sum("Provider" in prompt for prompt in asked) == 1
    chat_requests = [row for row in upstream.seen if row[0] == "POST"]
    assert len(chat_requests) == 2
    output = "\n".join(say_lines) + "\n".join(asked)
    assert "bad-key-1234567" not in output
    assert "good-key-1234567" not in output
    assert "****4567" in output


def test_summary_can_change_one_setting_without_restarting(upstream: Any) -> None:
    ask, asked, leftover = _scripted(["3", upstream.base, "", "1", "", "model", "", "2", "", ""])
    result = DEMO.interactive_model_setup(
        ask=ask, secret=lambda prompt: KEY, say=lambda _line: None
    )

    assert leftover == []
    assert result is not None
    assert result[DEMO.MODEL_KEYS[2]] == GOOD_IDS[1]
    assert sum("base URL" in prompt for prompt in asked) == 1


def test_cancelling_the_summary_saves_nothing(upstream: Any) -> None:
    ask, _asked, _ = _scripted(["3", upstream.base, "", "1", "", "n"])

    with pytest.raises(RuntimeError, match="Nothing was saved"):
        DEMO.interactive_model_setup(ask=ask, secret=lambda prompt: KEY, say=lambda _line: None)


def test_provider_menu_fills_the_preset_url_and_fake_returns_none() -> None:
    ask, _asked, _ = _scripted(["1"])
    preset = DEMO.PROVIDER_PRESETS[0]
    assert DEMO.choose_provider(ask, lambda _line: None) == (preset[0], preset[1])

    ask, _asked, _ = _scripted(["4"])
    assert DEMO.choose_provider(ask, lambda _line: None) is None
