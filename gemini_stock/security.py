from __future__ import annotations

import os
from pathlib import Path


SECRET_KEYS = {
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "POLYGON_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "FEISHU_WEBHOOK_URL",
    "WECOM_WEBHOOK_URL",
}


def find_secret_config_warnings(env_path: str | Path = ".env") -> list[str]:
    path = Path(env_path)
    warnings: list[str] = []
    include_runtime = not path.name.endswith(".example")
    if not path.exists():
        return _runtime_secret_warnings() if include_runtime else []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key in SECRET_KEYS and _looks_sensitive(value):
            warnings.append(key)
    if include_runtime:
        warnings.extend(_runtime_secret_warnings())
    return sorted(set(warnings))


def _runtime_secret_warnings() -> list[str]:
    return [
        key
        for key in SECRET_KEYS
        if _looks_sensitive(os.environ.get(key, ""))
    ]


def _looks_sensitive(value: str) -> bool:
    if not value:
        return False
    placeholder_words = ["你的", "example", "changeme", "placeholder", "test"]
    lowered = value.lower()
    if any(word in lowered for word in placeholder_words):
        return False
    return len(value) >= 8
