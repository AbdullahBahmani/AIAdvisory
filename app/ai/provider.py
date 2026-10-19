"""Minimal OpenAI chat-completions adapter; stdlib-only and optional by default.

Provider responses are *never* trusted as domain facts. Parsing, size limits and schema
validation happen in the service boundary; no tools are exposed to the provider.
"""

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import HTTPException

# Fixed provider host: do not accept arbitrary user URL (SSRF/credential exfiltration).
ENDPOINT = "https://api.openai.com/v1/chat/completions"


class ProviderFailure(Exception):
    """Safe, provider-independent failure, never containing response bodies or keys."""


@dataclass(frozen=True)
class ProviderResult:
    content: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class AIProvider(Protocol):
    def generate(self, system: str, user: str, schema: dict) -> ProviderResult: ...


class OpenAIProvider:
    def __init__(self, api_key: str, model: str, timeout: float = 12.0):
        if not api_key:
            raise ValueError("Provider key required")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate(self, system: str, user: str, schema: dict) -> ProviderResult:
        # JSON mode plus independent Pydantic validation supports models without strict-schema mode.
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "max_completion_tokens": 1300,
            "store": False,
        }
        req = Request(
            ENDPOINT,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key},
        )
        # One retry, only on throttling/temporary server errors, with a short bounded delay.
        for attempt in range(2):
            try:
                with urlopen(req, timeout=self.timeout) as response:
                    if int(response.headers.get("Content-Length", 0)) > 65536:
                        raise ProviderFailure("Provider response too large")
                    data = response.read(65537)
                if len(data) > 65536:
                    raise ProviderFailure("Provider response too large")
                parsed = json.loads(data)
                choice = parsed["choices"][0]["message"]
                if choice.get("refusal") or not isinstance(choice.get("content"), str):
                    raise ProviderFailure("Provider refused or omitted structured output")
                usage = parsed.get("usage") or {}
                return ProviderResult(
                    content=choice["content"],
                    provider="openai",
                    model=self.model,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                )
            except HTTPError as exc:
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code <= 599):
                    time.sleep(0.3)
                    continue
                raise ProviderFailure("Provider HTTP error") from None
            except (
                URLError,
                OSError,
                TimeoutError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                json.JSONDecodeError,
            ):
                # The original provider exception is deliberately not re-raised or
                # chained: its text can echo response fragments or credentials.
                # The failure itself is not swallowed - it is propagated as a
                # ProviderFailure and surfaced by the caller as a safe HTTP 502.
                raise ProviderFailure("Provider transport or response error") from None
        raise ProviderFailure("Provider unavailable")


def get_ai_provider() -> AIProvider:
    # Dependency injection allows offline mock providers in tests, with no actual external calls.
    if os.getenv("ADVISORY_AI_ENABLED") != "1":
        raise HTTPException(
            status_code=503, detail="Optional AI disabled; rules-only mode remains available"
        )
    key = os.getenv("ADVISORY_AI_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="AI API key not configured")
    return OpenAIProvider(key, os.getenv("ADVISORY_AI_MODEL", "gpt-4.1-mini"))
