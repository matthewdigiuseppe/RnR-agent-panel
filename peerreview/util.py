"""Small shared helpers: time, ids, hashing, crude token accounting."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id(project: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{slugify(project)}-{uuid.uuid4().hex[:6]}"


def slugify(text: str, maxlen: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:maxlen] or "run"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=False, default=str)


def jload(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def estimate_tokens(text: str) -> int:
    """Provider-neutral rough token estimate (~4 characters per token)."""
    return max(1, len(text) // 4)


def truncate_tokens(text: str, max_tokens: int, marker: str = "\n[... truncated ...]\n") -> str:
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return text[:head] + marker + text[-tail:]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def clip_words(text: str, max_words: int) -> tuple[str, bool]:
    """Clip to a word budget. Returns (text, was_clipped)."""
    words = text.split()
    if len(words) <= max_words:
        return text, False
    return " ".join(words[:max_words]) + " [...]", True
