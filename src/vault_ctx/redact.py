from __future__ import annotations

import re

REDACTED = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)(\s*[:=]\s*)(['\"]?)[^\s'\"]{8,}"),
    re.compile(r"(?i)^(\s*[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD)[A-Z0-9_]*\s*=\s*).+$", re.M),
    re.compile(r"(?i)(https?://[^/\s:@]+):[^/\s@]+@"),
]


def redact(text: str) -> tuple[str, int]:
    if not text:
        return text, 0
    total = 0
    result = text
    for pattern in _PATTERNS:
        result, count = pattern.subn(_replacement, result)
        total += count
    return result, total


def _replacement(match: re.Match[str]) -> str:
    groups = match.groups()
    if not groups:
        return REDACTED
    if len(groups) == 1:
        return f"{groups[0]} {REDACTED}"
    if len(groups) == 2:
        return f"{groups[0]}{groups[1]}{REDACTED}"
    if len(groups) == 3:
        return f"{groups[0]}{groups[1]}{groups[2]}{REDACTED}"
    return f"{groups[0]}{REDACTED}"


def redact_tree(value):
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [redact_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    return value
