"""Conservative provider-input screening, not an enterprise DLP system.

Only the bounded context that is about to leave for a model is scanned.
False negatives are possible; external provider governance remains required.
"""

from __future__ import annotations

import json
import re

from fastapi import HTTPException

_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)
_API_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*[^\s,;]{8,})",
    re.IGNORECASE,
)


def assert_no_obvious_sensitive_data(context: dict) -> None:
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    if any(pattern.search(serialized) for pattern in (_EMAIL, _PRIVATE_KEY, _API_TOKEN)):
        raise HTTPException(
            status_code=403,
            detail=(
                "Potential personal data or credential in AI context; "
                "do not send to external provider"
            ),
        )
