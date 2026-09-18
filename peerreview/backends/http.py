"""Minimal JSON-over-HTTPS helper (stdlib only, honours proxy env vars)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import BackendError


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str],
              timeout: float = 180.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        retryable = exc.code in {408, 409, 425, 429, 500, 502, 503, 504, 529}
        raise BackendError(f"HTTP {exc.code} from {url}: {detail}", retryable=retryable) from exc
    except urllib.error.URLError as exc:
        raise BackendError(f"network error calling {url}: {exc.reason}", retryable=True) from exc
