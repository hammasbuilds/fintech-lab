"""A local model, over HTTP, with no dependencies and a cache that makes runs repeatable.

Projects 01-03 need no model at all. Projects 04-06 ask one to do the same jobs and are
scored against those rule-based versions, which is the only reason the model numbers mean
anything: there is a deterministic baseline on the same cases.

Three decisions worth stating:

* **Ollama is reached with ``urllib``**, not a client library. The repo's claim is that
  nothing but the standard library runs, and a model served over HTTP does not change
  that. It also means no version of a client package can drift from the server.
* **Every reply is cached on disk, keyed by a digest of everything that produced it** -
  model, prompt, system prompt and options. The cache is committed, so the published
  numbers can be rechecked by anyone without a GPU, and a re-run costs nothing. Change a
  prompt and the digest changes, so a stale answer cannot be served for a new question.
* **Sampling is pinned**: temperature 0 and a fixed seed. A benchmark whose numbers move
  between runs cannot be compared against a baseline that does not move.

When the model is absent and the cache has no entry, this raises rather than returning a
plausible blank. A benchmark that silently scores an empty string reports the model as
very bad at the task instead of reporting that it was never asked.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "llm_cache"
DEFAULT_HOST = "http://127.0.0.1:11434"

#: Sizes chosen per task rather than left at a default. A cap below the length of a
#: correct answer turns a right answer into an unparseable one, and that failure looks
#: exactly like the model not knowing the task.
SHORT = 160
MEDIUM = 512
LONG = 1200


class ModelUnavailable(Exception):
    """No cached reply and no reachable model. Never returns an empty answer instead."""


@dataclass(frozen=True)
class Options:
    temperature: float = 0.0
    seed: int = 7
    num_predict: int = MEDIUM
    #: Ollama's own JSON mode. Useful, and not trusted: the output is still parsed and
    #: validated by the caller, because "valid JSON" and "the right answer" differ.
    json_mode: bool = False
    stop: tuple[str, ...] = ()

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {
            "temperature": self.temperature,
            "seed": self.seed,
            "num_predict": self.num_predict,
        }
        if self.stop:
            out["stop"] = list(self.stop)
        return out


@dataclass
class Reply:
    text: str
    model: str
    cached: bool
    duration_ms: float
    prompt_tokens: int = 0
    response_tokens: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "cached": self.cached,
            "duration_ms": round(self.duration_ms, 1),
            "prompt_tokens": self.prompt_tokens,
            "response_tokens": self.response_tokens,
            "truncated": self.truncated,
            "characters": len(self.text),
        }


@dataclass
class Usage:
    """What a benchmark run actually cost, so a reported number has a price beside it."""

    calls: int = 0
    cached: int = 0
    generated: int = 0
    truncated: int = 0
    total_ms: float = 0.0
    response_tokens: int = 0
    models: set[str] = field(default_factory=set)

    def record(self, reply: Reply) -> None:
        self.calls += 1
        self.cached += int(reply.cached)
        self.generated += int(not reply.cached)
        self.truncated += int(reply.truncated)
        self.total_ms += reply.duration_ms
        self.response_tokens += reply.response_tokens
        self.models.add(reply.model)

    def as_dict(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "served_from_cache": self.cached,
            "generated": self.generated,
            "hit_the_token_cap": self.truncated,
            "wall_clock_seconds": round(self.total_ms / 1000, 1),
            "response_tokens": self.response_tokens,
            "models": sorted(self.models),
        }


# ------------------------------------------------------------------ the server


def tags(host: str = DEFAULT_HOST, timeout: float = 3.0) -> list[str]:
    """Model names the server has, or an empty list when it cannot be reached."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
    return sorted(entry.get("name", "") for entry in data.get("models", []))


def available(model: str, host: str = DEFAULT_HOST) -> bool:
    return model in tags(host)


def resident(host: str = DEFAULT_HOST, timeout: float = 3.0) -> list[dict[str, object]]:
    """Models currently loaded, so a benchmark can prefer one that is already in memory."""
    try:
        with urllib.request.urlopen(f"{host}/api/ps", timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return []
    return data.get("models", [])


# -------------------------------------------------------------------- the cache


def digest(model: str, prompt: str, system: str, options: Options) -> str:
    body = json.dumps(
        {
            "model": model,
            "system": system,
            "prompt": prompt,
            "options": options.payload(),
            "json_mode": options.json_mode,
            # Part of the key: replies from the completion endpoint and the chat endpoint
            # are not interchangeable, and an early version of this cached one as the
            # other.
            "endpoint": "chat",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:40]


def _cache_path(key: str) -> Path:
    return CACHE / f"{key}.json"


def read_cache(key: str) -> dict[str, object] | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_cache(key: str, entry: dict[str, object]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(
        json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def cache_size() -> dict[str, object]:
    if not CACHE.is_dir():
        return {"entries": 0, "bytes": 0}
    files = list(CACHE.glob("*.json"))
    return {"entries": len(files), "bytes": sum(path.stat().st_size for path in files)}


# ------------------------------------------------------------------- generating


def generate(
    model: str,
    prompt: str,
    *,
    system: str = "",
    options: Options | None = None,
    host: str = DEFAULT_HOST,
    timeout: float = 300.0,
    usage: Usage | None = None,
    refresh: bool = False,
    keep_alive: str = "20m",
) -> Reply:
    """Ask ``model`` for one completion, from cache when it has been asked before."""
    opts = options or Options()
    key = digest(model, prompt, system, opts)

    if not refresh:
        cached = read_cache(key)
        if cached is not None:
            reply = Reply(
                text=str(cached.get("response", "")),
                model=model,
                cached=True,
                duration_ms=0.0,
                prompt_tokens=int(cached.get("prompt_tokens", 0)),
                response_tokens=int(cached.get("response_tokens", 0)),
                truncated=bool(cached.get("truncated", False)),
            )
            if usage is not None:
                usage.record(reply)
            return reply

    # /api/chat, not /api/generate: the chat endpoint applies the model's own template,
    # and without it an instruction is just text to continue. The first version of this
    # used the completion endpoint and a base model answered "Reply with exactly: OK"
    # with "88888888" - which reads as a model failing the task rather than as the
    # template never being applied.
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": opts.payload(),
        # Hold the model in memory between calls. Without this a benchmark pays the load
        # cost on every prompt: measured at about 40 seconds a call on this machine, which
        # is most of the run and none of the work.
        "keep_alive": keep_alive,
    }
    if opts.json_mode:
        body["format"] = "json"

    request = urllib.request.Request(  # noqa: S310
        f"{host}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        raise ModelUnavailable(
            f"{model} could not be reached at {host} and this prompt is not cached "
            f"({type(exc).__name__}: {exc}). Start ollama, or run with the committed "
            f"cache only."
        ) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000

    text = str((data.get("message") or {}).get("content", ""))
    response_tokens = int(data.get("eval_count", 0))
    truncated = response_tokens >= opts.num_predict
    entry = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "options": opts.payload(),
        "json_mode": opts.json_mode,
        "response": text,
        "prompt_tokens": int(data.get("prompt_eval_count", 0)),
        "response_tokens": response_tokens,
        "truncated": truncated,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_cache(key, entry)

    reply = Reply(
        text=text,
        model=model,
        cached=False,
        duration_ms=elapsed_ms,
        prompt_tokens=int(data.get("prompt_eval_count", 0)),
        response_tokens=response_tokens,
        truncated=truncated,
    )
    if usage is not None:
        usage.record(reply)
    return reply


def first_block(text: str, fence: str = "") -> str:
    """Pull a fenced block out of a reply, or return the reply stripped.

    Models wrap answers in prose and code fences whatever the instructions say, and a
    grader that treats the fence as part of the answer measures formatting rather than
    the task.
    """
    marker = "```"
    if marker not in text:
        return text.strip()
    parts = text.split(marker)
    for index in range(1, len(parts), 2):
        block = parts[index]
        head, _, rest = block.partition("\n")
        if fence and head.strip().lower() not in {fence, ""}:
            continue
        return (rest if rest else head).strip()
    return text.strip()


def extract_json(text: str) -> object:
    """Parse the first JSON value in a reply. Raises ``ValueError`` with the text."""
    candidate = first_block(text, "json")
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON value in the reply: {candidate[:200]!r}")
