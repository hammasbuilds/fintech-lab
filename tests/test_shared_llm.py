"""The model client: the digest, the cache, the parsing helpers, and the refusal to guess.

None of these need a model. The one thing that must be true without one is that an
uncached prompt raises instead of returning an empty answer - a benchmark that scores a
blank string reports the model as very bad at the task rather than reporting that it was
never asked.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from shared import llm


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "CACHE", tmp_path / "cache")
    return tmp_path / "cache"


# -------------------------------------------------------------------- options


def test_options_pin_sampling_so_runs_are_comparable() -> None:
    payload = llm.Options().payload()
    assert payload["temperature"] == 0.0
    assert payload["seed"] == 7
    assert "num_predict" in payload


def test_stop_sequences_are_passed_only_when_set() -> None:
    assert "stop" not in llm.Options().payload()
    assert llm.Options(stop=("END",)).payload()["stop"] == ["END"]


# --------------------------------------------------------------------- digest


def test_the_digest_covers_everything_that_produced_the_answer() -> None:
    base = llm.digest("m", "prompt", "system", llm.Options())
    assert base == llm.digest("m", "prompt", "system", llm.Options())
    assert base != llm.digest("m2", "prompt", "system", llm.Options())
    assert base != llm.digest("m", "prompt2", "system", llm.Options())
    assert base != llm.digest("m", "prompt", "system2", llm.Options())
    assert base != llm.digest("m", "prompt", "system", llm.Options(temperature=0.7))
    assert base != llm.digest("m", "prompt", "system", llm.Options(num_predict=99))
    assert base != llm.digest("m", "prompt", "system", llm.Options(json_mode=True))


def test_changing_a_prompt_cannot_serve_the_old_answer(cache) -> None:
    # The whole point of keying on the inputs: edit the prompt, get a cache miss.
    llm.write_cache(llm.digest("m", "old", "", llm.Options()), {"response": "stale"})
    assert llm.read_cache(llm.digest("m", "new", "", llm.Options())) is None


# ---------------------------------------------------------------------- cache


def test_a_reply_is_written_and_read_back(cache) -> None:
    key = llm.digest("m", "p", "", llm.Options())
    llm.write_cache(key, {"response": "hello", "response_tokens": 2})
    entry = llm.read_cache(key)
    assert entry is not None
    assert entry["response"] == "hello"
    assert llm.cache_size()["entries"] == 1


def test_a_corrupt_cache_entry_is_a_miss_not_a_crash(cache) -> None:
    key = llm.digest("m", "p", "", llm.Options())
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{key}.json").write_text("{not json", encoding="utf-8")
    assert llm.read_cache(key) is None


def test_the_cache_is_written_with_line_feeds(cache) -> None:
    # It is committed, and CI compares it. Letting Windows translate the endings makes an
    # identical file look modified on one platform and clean on the other.
    key = llm.digest("m", "p", "", llm.Options())
    llm.write_cache(key, {"response": "a\nb"})
    assert b"\r\n" not in (cache / f"{key}.json").read_bytes()


def test_an_empty_cache_reports_zero_rather_than_failing(cache) -> None:
    assert llm.cache_size() == {"entries": 0, "bytes": 0}


# ----------------------------------------------------------------- generating


def test_a_cached_prompt_needs_no_server(cache, monkeypatch) -> None:
    def explode(*_args, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("the server was contacted for a cached prompt")

    monkeypatch.setattr(llm.urllib.request, "urlopen", explode)
    key = llm.digest("m", "p", "", llm.Options())
    llm.write_cache(key, {"response": "cached", "response_tokens": 3, "truncated": False})
    reply = llm.generate("m", "p")
    assert reply.text == "cached"
    assert reply.cached is True
    assert reply.duration_ms == 0.0


def test_an_uncached_prompt_with_no_server_raises(cache, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise urllib.error.URLError("nothing listening")

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    with pytest.raises(llm.ModelUnavailable, match="not cached"):
        llm.generate("m", "never asked before")


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


def test_a_generated_reply_is_cached_and_counted(cache, monkeypatch) -> None:
    calls: list[dict] = []

    def answer(request, timeout=None):
        calls.append(json.loads(request.data))
        return _Response(
            {
                "message": {"content": "the answer"},
                "eval_count": 5,
                "prompt_eval_count": 11,
            }
        )

    monkeypatch.setattr(llm.urllib.request, "urlopen", answer)
    usage = llm.Usage()
    reply = llm.generate("m", "p", system="s", options=llm.Options(num_predict=50), usage=usage)

    assert reply.text == "the answer"
    assert reply.cached is False
    assert reply.response_tokens == 5
    assert reply.prompt_tokens == 11
    assert reply.truncated is False
    # The chat endpoint, with the system prompt as a message - the completion endpoint
    # applies no template and a base model answers an instruction with gibberish.
    assert calls[0]["messages"][0] == {"role": "system", "content": "s"}
    assert calls[0]["messages"][1] == {"role": "user", "content": "p"}
    assert "keep_alive" in calls[0]
    assert usage.calls == 1 and usage.generated == 1 and usage.cached == 0

    # And the second call comes from the cache.
    again = llm.generate("m", "p", system="s", options=llm.Options(num_predict=50), usage=usage)
    assert again.cached is True
    assert usage.cached == 1


def test_hitting_the_token_cap_is_recorded(cache, monkeypatch) -> None:
    # A cap below the length of a correct answer turns a right answer into an
    # unparseable one, and that failure looks exactly like not knowing the task.
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda *a, **k: _Response({"message": {"content": "cut off"}, "eval_count": 20}),
    )
    usage = llm.Usage()
    reply = llm.generate("m", "p", options=llm.Options(num_predict=20), usage=usage)
    assert reply.truncated is True
    assert usage.truncated == 1
    assert usage.as_dict()["hit_the_token_cap"] == 1


def test_usage_reports_what_a_run_cost(cache) -> None:
    usage = llm.Usage()
    usage.record(llm.Reply("a", "m1", False, 1200.0, 0, 7))
    usage.record(llm.Reply("b", "m2", True, 0.0, 0, 3))
    data = usage.as_dict()
    assert data["calls"] == 2
    assert data["generated"] == 1
    assert data["served_from_cache"] == 1
    assert data["wall_clock_seconds"] == 1.2
    assert data["response_tokens"] == 10
    assert data["models"] == ["m1", "m2"]


# ------------------------------------------------------------------ the server


def test_an_unreachable_server_lists_no_models(monkeypatch) -> None:
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("down")),
    )
    assert llm.tags() == []
    assert llm.available("anything") is False
    assert llm.resident() == []


def test_model_names_come_back_sorted(monkeypatch) -> None:
    monkeypatch.setattr(
        llm.urllib.request,
        "urlopen",
        lambda *a, **k: _Response({"models": [{"name": "b"}, {"name": "a"}]}),
    )
    assert llm.tags() == ["a", "b"]
    assert llm.available("a") is True


# ------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("plain", "plain"),
        ("```\ncode\n```", "code"),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ("before ```\ninside\n``` after", "inside"),
    ],
)
def test_a_fenced_block_is_pulled_out(text: str, expected: str) -> None:
    # Models wrap answers in fences whatever the instructions say, and a grader that
    # treats the fence as part of the answer measures formatting rather than the task.
    assert llm.first_block(text) == expected


def test_json_is_found_inside_prose() -> None:
    assert llm.extract_json('here you go: {"a": 1} hope that helps') == {"a": 1}
    assert llm.extract_json("```json\n[1, 2]\n```") == [1, 2]


def test_text_with_no_json_raises_with_the_text() -> None:
    with pytest.raises(ValueError, match="no JSON value"):
        llm.extract_json("I cannot do that")


def test_the_budgets_are_named_rather_than_magic() -> None:
    assert llm.SHORT < llm.MEDIUM < llm.LONG
