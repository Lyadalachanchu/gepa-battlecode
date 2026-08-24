"""LunaClient — the single LLM interface for the experiment.

Wraps the OpenAI Responses API (openai SDK v3.x) with:

- structured outputs via ``text.format = {type: json_schema, strict: true}``
  using :data:`model.schemas.REFLECT_AND_PATCH_SCHEMA`;
- ``reasoning = {"effort": <from configs/model.lock.json>}``;
- 3 attempts with exponential backoff on transient transport errors;
- one JSONL log line per logical call (prompt sha256, token usage, model id,
  UTC timestamp) appended to the call-log path given at construction;
- an injectable transport so tests never touch the network.  The transport is
  a callable ``(request: dict) -> dict`` returning at least
  ``{"output_text": str, "usage": {"input_tokens", "output_tokens",
  "reasoning_tokens"}, "model": str}``.

The API key is read from ``OPENAI_API_KEY`` only inside the default transport,
at call time — never at import or construction time.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .schemas import REFLECT_AND_PATCH_SCHEMA, SCHEMA_NAME, SchemaError, parse_and_validate

__all__ = ["LunaClient", "ModelCall", "TransientTransportError", "DEFAULT_LOCK_PATH"]

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK_PATH = _REPO_ROOT / "configs" / "model.lock.json"


class TransientTransportError(RuntimeError):
    """A retryable transport failure (connection reset, 429, 5xx, timeout)."""


class Transport(Protocol):
    def __call__(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class ModelCall:
    """Result of one logical reflect-and-patch call (retries included)."""

    parsed: dict[str, Any] | None
    raw_text: str
    usage: dict[str, int]
    model_id: str
    error: str | None = None


def _empty_usage() -> dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}


def _openai_transport(request: dict[str, Any]) -> dict[str, Any]:
    """Default transport: real OpenAI Responses API call.

    Imports openai and reads OPENAI_API_KEY lazily, at call time.  Raises
    :class:`TransientTransportError` on retryable failures.
    """
    import openai  # local import: tests must not need network or a key

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in the environment")

    client = openai.OpenAI(api_key=api_key)
    try:
        response = client.responses.create(**request)
    except (
        openai.APIConnectionError,  # includes APITimeoutError
        openai.RateLimitError,
        openai.InternalServerError,
    ) as exc:
        raise TransientTransportError(f"{type(exc).__name__}: {exc}") from exc
    except openai.APIStatusError as exc:
        if exc.status_code >= 500:
            raise TransientTransportError(f"{type(exc).__name__}: {exc}") from exc
        raise

    usage = _empty_usage()
    if response.usage is not None:
        usage["input_tokens"] = response.usage.input_tokens
        usage["output_tokens"] = response.usage.output_tokens
        details = response.usage.output_tokens_details
        usage["reasoning_tokens"] = details.reasoning_tokens if details is not None else 0

    return {
        "output_text": response.output_text,
        "usage": usage,
        "model": str(response.model),
    }


@dataclass
class LunaClient:
    """Client for gpt-5.6-luna via the OpenAI Responses API."""

    model_id: str
    reasoning_effort: str
    call_log_path: str | Path
    transport: Transport = field(default=_openai_transport)
    max_output_tokens: int | None = None
    max_attempts: int = 3
    backoff_base_seconds: float = 1.0
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time

    @classmethod
    def from_lock(
        cls,
        call_log_path: str | Path,
        lock_path: str | Path = DEFAULT_LOCK_PATH,
        transport: Transport | None = None,
        **overrides: Any,
    ) -> "LunaClient":
        """Build a client from configs/model.lock.json (model id, effort, caps)."""
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        kwargs: dict[str, Any] = {
            "model_id": lock["model"],
            "reasoning_effort": lock["reasoning_effort"],
            "call_log_path": call_log_path,
            "max_output_tokens": lock.get("max_output_tokens"),
        }
        if transport is not None:
            kwargs["transport"] = transport
        kwargs.update(overrides)
        return cls(**kwargs)

    # ------------------------------------------------------------------ calls

    def reflect_and_patch(self, system_prompt: str, user_content: str) -> ModelCall:
        """Run one structured reflect-and-patch call.

        Retries transient transport failures up to ``max_attempts`` times with
        exponential backoff.  Always appends exactly one JSONL log line.
        Never raises on model/transport failure — errors land in
        ``ModelCall.error`` with ``parsed=None``.
        """
        request = self._build_request(system_prompt, user_content)
        prompt_sha = hashlib.sha256(
            (system_prompt + "\x00" + user_content).encode("utf-8")
        ).hexdigest()

        raw_text = ""
        usage = _empty_usage()
        model_id = self.model_id
        parsed: dict[str, Any] | None = None
        error: str | None = None
        attempts = 0

        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            try:
                result = self.transport(request)
            except TransientTransportError as exc:
                error = f"transient transport error (attempt {attempt}/{self.max_attempts}): {exc}"
                if attempt < self.max_attempts:
                    self.sleep(self.backoff_base_seconds * (2 ** (attempt - 1)))
                continue
            except Exception as exc:  # non-retryable
                error = f"transport error: {type(exc).__name__}: {exc}"
                break

            raw_text = str(result.get("output_text", ""))
            got_usage = result.get("usage") or {}
            for key in usage:
                usage[key] = int(got_usage.get(key, 0))
            model_id = str(result.get("model", self.model_id))
            try:
                parsed = parse_and_validate(raw_text)
                error = None
            except SchemaError as exc:
                parsed = None
                error = f"schema validation failed: {exc}"
            break

        call = ModelCall(
            parsed=parsed, raw_text=raw_text, usage=usage, model_id=model_id, error=error
        )
        self._log_call(prompt_sha=prompt_sha, call=call, attempts=attempts)
        return call

    # -------------------------------------------------------------- internals

    def _build_request(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": self.model_id,
            "instructions": system_prompt,
            "input": user_content,
            "reasoning": {"effort": self.reasoning_effort},
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": SCHEMA_NAME,
                    "strict": True,
                    "schema": REFLECT_AND_PATCH_SCHEMA,
                }
            },
        }
        if self.max_output_tokens is not None:
            request["max_output_tokens"] = self.max_output_tokens
        return request

    def _log_call(self, prompt_sha: str, call: ModelCall, attempts: int) -> None:
        line = {
            "timestamp": self.clock(),
            "model_id": call.model_id,
            "prompt_sha256": prompt_sha,
            "usage": call.usage,
            "attempts": attempts,
            "parsed_ok": call.parsed is not None,
            "error": call.error,
        }
        path = Path(self.call_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")
