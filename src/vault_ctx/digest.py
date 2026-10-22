from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import Config
from .redact import redact
from .sources.sqlite_db import MessageInfo, SessionInfo

LABELS = {
    "en": {
        "handoff": "Handoff",
        "goal": "Goal",
        "state": "Current state",
        "decisions": "Decisions",
        "files": "Key files",
        "transcript": "Recent transcript",
        "user": "User",
        "assistant": "Assistant",
        "meta": "Session",
        "none": "_no data_",
        "truncated": "… [truncated]",
    },
    "ru": {
        "handoff": "Передача контекста",
        "goal": "Задача",
        "state": "Текущее состояние",
        "decisions": "Решения",
        "files": "Ключевые файлы",
        "transcript": "Последние сообщения",
        "user": "Пользователь",
        "assistant": "Ассистент",
        "meta": "Сессия",
        "none": "_нет данных_",
        "truncated": "… [обрезано]",
    },
}

_FILE_KEYS = ("filePath", "filepath", "path", "file", "filename", "notebook_path")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _truncate(text: str, limit: int, marker: str = "… [truncated]") -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker) - 2)].rstrip() + "\n\n" + marker


def collapse_part(part: dict, labels: dict) -> str | None:
    kind = part.get("type")
    if kind == "text":
        text = (part.get("text") or "").strip()
        return text or None
    if kind == "tool":
        name = part.get("tool") or "tool"
        state = part.get("state") or {}
        title = state.get("title") or ""
        status = state.get("status")
        line = f"[{name}] {title}".strip()
        if status and status != "completed":
            line += f" ({status})"
        return _truncate(line, 200, labels["truncated"])
    if kind == "file":
        name = part.get("filename") or part.get("path") or "file"
        return f"[file] {name}"
    return None


def message_text(message: MessageInfo, labels: dict) -> str:
    chunks = []
    for part in message.parts:
        rendered = collapse_part(part, labels)
        if rendered:
            chunks.append(rendered)
    return "\n".join(chunks)


def transcript(messages: list[MessageInfo], max_chars: int, labels: dict) -> str:
    if max_chars <= 0:
        return ""
    chunks: list[str] = []
    used = 0
    for message in reversed(messages):
        text = message_text(message, labels)
        if not text:
            continue
        speaker = labels["user"] if message.role == "user" else labels["assistant"]
        block = f"**{speaker}:**\n{text}"
        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 200:
                chunks.append(_truncate(block, remaining, labels["truncated"]))
            break
        chunks.append(block)
        used += len(block) + 2
    return "\n\n".join(reversed(chunks))


def touched_files(messages: list[MessageInfo], limit: int = 15) -> list[str]:
    seen: list[str] = []
    for message in messages:
        for part in message.parts:
            if part.get("type") != "tool":
                continue
            payload = (part.get("state") or {}).get("input") or {}
            for key in _FILE_KEYS:
                value = payload.get(key)
                if not isinstance(value, str) or not value:
                    continue
                normalized = value.replace("\\", "/")
                if "node_modules/" in normalized or "/.venv/" in normalized:
                    break
                if value not in seen:
                    seen.append(value)
                break
            if len(seen) >= limit:
                return seen
    return seen


def first_user_text(messages: list[MessageInfo], labels: dict) -> str:
    for message in messages:
        if message.role != "user":
            continue
        text = message_text(message, labels)
        if text:
            return text
    return ""


def last_assistant_text(messages: list[MessageInfo], labels: dict) -> str:
    for message in reversed(messages):
        if message.role != "assistant":
            continue
        chunks = [
            (part.get("text") or "").strip()
            for part in message.parts
            if part.get("type") == "text"
        ]
        text = "\n".join(chunk for chunk in chunks if chunk)
        if text:
            return text
    return ""


def decisions_tail(cfg: Config, count: int = 5) -> str:
    text = ""
    try:
        text = Path(cfg.decisions_file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    blocks = re.findall(r"(?ms)^## (?!\#).*?(?=^## |\Z)", text)
    blocks = [block.strip() for block in blocks if block.strip()]
    return "\n\n".join(blocks[-count:])


def display_title(session: SessionInfo, messages: list[MessageInfo], labels: dict) -> str:
    title = (session.title or "").strip()
    if title and not title.lower().startswith("new session"):
        return title
    goal = first_user_text(messages, labels)
    first_line = next((line for line in goal.splitlines() if line.strip()), "")
    first_line = re.sub(r"\s+", " ", first_line).strip()
    if first_line:
        return first_line[:70].rstrip() + ("..." if len(first_line) > 70 else "")
    return title or session.id


def _fit_sections(sections: list[dict], budget: int, marker: str) -> list[dict]:
    while sum(len(section["body"]) for section in sections) > budget:
        candidates = [
            (index, len(section["body"]) - section["min"])
            for index, section in enumerate(sections)
            if len(section["body"]) > section["min"]
        ]
        if not candidates:
            break
        index, _ = max(candidates, key=lambda item: item[1])
        excess = sum(len(section["body"]) for section in sections) - budget
        target = max(sections[index]["min"], len(sections[index]["body"]) - excess)
        sections[index]["body"] = _truncate(sections[index]["body"], target, marker)
    while sum(len(section["body"]) for section in sections) > budget and len(sections) > 1:
        sections.pop()
    return sections


def build_digest(
    cfg: Config,
    session: SessionInfo,
    messages: list[MessageInfo],
    max_tokens: int | None = None,
    lang: str | None = None,
    frontmatter: bool = True,
) -> tuple[str, int]:
    language = (lang or cfg.lang).lower()
    labels = LABELS.get(language, LABELS["en"])
    limit = max_tokens or cfg.max_tokens
    budget = limit * 4

    sections: list[dict] = []
    goal = first_user_text(messages, labels)
    if goal:
        sections.append(
            {"title": labels["goal"], "body": _truncate(goal, 900, labels["truncated"]), "min": 150}
        )
    state = last_assistant_text(messages, labels)
    if state:
        sections.append(
            {"title": labels["state"], "body": _truncate(state, 1800, labels["truncated"]), "min": 200}
        )
    decisions = decisions_tail(cfg)
    if decisions:
        sections.append(
            {"title": labels["decisions"], "body": _truncate(decisions, 900, labels["truncated"]), "min": 120}
        )
    files = touched_files(messages)
    if files:
        body = "\n".join(f"- `{path}`" for path in files)
        sections.append({"title": labels["files"], "body": body, "min": 60})

    used = sum(len(section["body"]) for section in sections) + 200
    remaining = max(600, budget - used)
    recent = transcript(messages, remaining, labels)
    if recent:
        sections.append({"title": labels["transcript"], "body": recent, "min": 300})

    sections = _fit_sections(sections, budget, labels["truncated"])

    header = []
    if frontmatter:
        date = datetime.fromtimestamp(session.time_created / 1000).strftime("%Y-%m-%d")
        header.extend(
            [
                "---",
                f"session_id: {session.id}",
                f"title: {json_scalar(session.title)}",
                f"project: {json_scalar(Path(session.directory).name or 'unknown')}",
                f"directory: {json_scalar(session.directory)}",
                f"created: {date}",
                f"tokens_input: {session.tokens_input}",
                f"tokens_output: {session.tokens_output}",
                f"cost: {round(session.cost, 6)}",
                "tags: [vault-ctx]",
                "---",
                "",
            ]
        )
    header.append(f"# {labels['handoff']}: {display_title(session, messages, labels)}")
    header.append("")
    models = sorted({message.model for message in messages if message.model})
    meta_bits = [f"`{session.id}`", session.directory]
    if models:
        meta_bits.append(", ".join(models))
    header.append(f"_{' · '.join(bits for bits in meta_bits if bits)}_")
    header.append("")

    body_parts = []
    for section in sections:
        body_parts.append(f"## {section['title']}\n\n{section['body'].strip()}")
    content = "\n".join(header).rstrip() + "\n\n" + "\n\n".join(body_parts) + "\n"
    content, _ = redact(content)
    return content, estimate_tokens(content)


def json_scalar(value: str) -> str:
    text = (value or "").replace("\n", " ").replace('"', '\\"')
    return f'"{text}"'


def build_handoff(
    cfg: Config,
    session: SessionInfo,
    messages: list[MessageInfo],
    max_tokens: int | None = None,
    lang: str | None = None,
) -> str:
    content, _ = build_digest(cfg, session, messages, max_tokens, lang, frontmatter=False)
    return content


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def summarize_for_log(text: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def session_display_title(cfg: Config, session: SessionInfo, messages: list[MessageInfo]) -> str:
    labels = LABELS.get((cfg.lang or "en").lower(), LABELS["en"])
    return display_title(session, messages, labels)
