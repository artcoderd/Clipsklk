"""One call, three providers. Each returns the parsed answer plus token usage.

Claude (default) uses the Anthropic SDK with structured outputs, prompt
caching on the transcript (re-asking for more clips of the same video within
an hour reads it from cache) and server-side refusal fallback. OpenAI and
Gemini use their own SDKs' structured-output helpers.
"""
import json
import logging
from dataclasses import dataclass
from typing import TypeVar

import httpx2
from pydantic import BaseModel, ValidationError

from app.ai import AIError
from app.ai.schema import json_schema
from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

PROVIDERS = ("anthropic", "openai", "gemini")
LABELS = {"anthropic": "Claude", "openai": "OpenAI", "gemini": "Gemini"}
# Models that accept server-side refusal fallback ("default" routing).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-2", "claude-fable-5-1", "claude-sonnet-5-5"}

# Tests inject mock transports here; production uses each SDK's default client.
# (All three SDKs are built on `httpx2`.)
HTTP_CLIENTS: dict[str, object] = {}


@dataclass
class AIResult:
    data: BaseModel
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0


def configured() -> list[str]:
    s = get_settings()
    keys = {"anthropic": s.anthropic_api_key, "openai": s.openai_api_key, "gemini": s.gemini_api_key}
    return [p for p in PROVIDERS if keys[p]]


def model_for(provider: str) -> str:
    s = get_settings()
    return {"anthropic": s.ai_model_anthropic, "openai": s.ai_model_openai, "gemini": s.ai_model_gemini}[provider]


def ask(provider: str, system: str, cached: str, request: str, out: type[T]) -> AIResult:
    """`cached`: the big, reusable part (transcript); `request`: the small, varying part."""
    if provider not in configured():
        raise AIError("ai_not_configured", provider)
    fn = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}[provider]
    return fn(system, cached, request, out)


def _parse(text: str, out: type[T]) -> T:
    try:
        return out.model_validate_json(text)
    except ValidationError as e:
        raise AIError("ai_bad_output", str(e)[:300]) from e


def _anthropic(system: str, cached: str, request: str, out: type[T]) -> AIResult:
    import anthropic

    s = get_settings()
    model = s.ai_model_anthropic
    client = anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=3, timeout=600.0,
                                 http_client=HTTP_CLIENTS.get("anthropic"))
    kwargs = {}
    if model in _FALLBACK_MODELS:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": [
                # The transcript is the long, stable part: cache it for an hour.
                {"type": "text", "text": cached, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": request},
            ]}],
            output_config={"effort": s.ai_effort, "format": {"type": "json_schema", "schema": json_schema(out)}},
            **kwargs,
        )
    except anthropic.AuthenticationError as e:
        raise AIError("ai_auth", str(e)) from e
    except anthropic.PermissionDeniedError as e:
        raise AIError("ai_auth", str(e)) from e
    except anthropic.RateLimitError as e:
        raise AIError("ai_rate_limited", str(e)) from e
    except anthropic.BadRequestError as e:
        raise AIError("ai_bad_request", str(e)) from e
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        raise AIError("ai_unavailable", str(e)) from e
    if resp.stop_reason == "refusal":
        raise AIError("ai_refused", str(getattr(resp.stop_details, "category", "")))
    if resp.stop_reason == "max_tokens":
        raise AIError("ai_truncated")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    u = resp.usage
    return AIResult(_parse(text, out), "anthropic", resp.model, u.input_tokens + (u.cache_creation_input_tokens or 0)
                    + (u.cache_read_input_tokens or 0), u.output_tokens, u.cache_read_input_tokens or 0)


def _openai(system: str, cached: str, request: str, out: type[T]) -> AIResult:
    import openai

    s = get_settings()
    client = openai.OpenAI(api_key=s.openai_api_key, max_retries=3, timeout=600.0,
                           http_client=HTTP_CLIENTS.get("openai"))
    try:
        resp = client.responses.parse(
            model=s.ai_model_openai,
            instructions=system,
            # Same order as Claude: the long transcript first so OpenAI's automatic prefix cache can reuse it.
            input=[{"role": "user", "content": [{"type": "input_text", "text": cached},
                                                {"type": "input_text", "text": request}]}],
            text_format=out,
            max_output_tokens=16000,
        )
    except openai.AuthenticationError as e:
        raise AIError("ai_auth", str(e)) from e
    except openai.RateLimitError as e:
        raise AIError("ai_rate_limited", str(e)) from e
    except openai.BadRequestError as e:
        raise AIError("ai_bad_request", str(e)) from e
    except (openai.APIStatusError, openai.APIConnectionError) as e:
        raise AIError("ai_unavailable", str(e)) from e
    data = resp.output_parsed
    if data is None:
        raise AIError("ai_refused" if any(getattr(c, "type", "") == "refusal" for o in resp.output
                                          for c in getattr(o, "content", []) or []) else "ai_bad_output")
    u = resp.usage
    cached_t = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
    return AIResult(data, "openai", resp.model, u.input_tokens, u.output_tokens, cached_t)


def _gemini(system: str, cached: str, request: str, out: type[T]) -> AIResult:
    from google import genai
    from google.genai import errors, types

    s = get_settings()
    mock = HTTP_CLIENTS.get("gemini")
    opts = types.HttpOptions(httpx_client=mock) if mock is not None else None
    client = genai.Client(api_key=s.gemini_api_key, http_options=opts)
    try:
        resp = client.models.generate_content(
            model=s.ai_model_gemini,
            contents=[cached, request],
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               response_schema=out, max_output_tokens=16000),
        )
    except errors.ClientError as e:
        code = {401: "ai_auth", 403: "ai_auth", 429: "ai_rate_limited"}.get(e.code, "ai_bad_request")
        raise AIError(code, str(e)) from e
    except (errors.APIError, httpx2.HTTPError) as e:
        raise AIError("ai_unavailable", str(e)) from e
    data = resp.parsed if isinstance(resp.parsed, out) else _parse(resp.text or "", out)
    u = resp.usage_metadata
    return AIResult(data, "gemini", s.ai_model_gemini, (u.prompt_token_count or 0) if u else 0,
                    (u.candidates_token_count or 0) if u else 0, (u.cached_content_token_count or 0) if u else 0)


def dumps(data: BaseModel) -> str:
    return json.dumps(data.model_dump(), ensure_ascii=False)
