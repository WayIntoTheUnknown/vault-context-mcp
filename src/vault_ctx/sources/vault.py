from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..config import Config

CONTEXT_START = "<!-- vault-ctx:auto:start -->"
CONTEXT_END = "<!-- vault-ctx:auto:end -->"

CONTEXT_TEMPLATE = """# Контекст ИИ

Краткая шапка: что важно помнить между сессиями. Правь руками — блок ниже перезаписывается автоматически.

<!-- vault-ctx:auto:start -->
<!-- vault-ctx:auto:end -->

## Заметки

"""


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


@contextmanager
def state_lock(cfg: Config, timeout: float = 10.0):
    lock_path = cfg.state_file.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    handle = None
    while handle is None:
        try:
            handle = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() - started > timeout:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                started = time.monotonic()
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(handle)
        try:
            lock_path.unlink()
        except OSError:
            pass


def load_state(cfg: Config) -> dict:
    raw = read_text(cfg.state_file, "")
    if not raw.strip():
        return {"version": 1, "sessions": {}, "decisions": {}}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {"version": 1, "sessions": {}, "decisions": {}}
    state.setdefault("version", 1)
    state.setdefault("sessions", {})
    state.setdefault("decisions", {})
    return state


def save_state(cfg: Config, state: dict) -> None:
    write_text(cfg.state_file, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def slugify(value: str, fallback: str = "session") -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return cleaned or fallback


def session_filename(title: str, slug: str, session_id: str, created_ms: int) -> str:
    date = datetime.fromtimestamp(created_ms / 1000).strftime("%Y-%m-%d")
    name = slugify(slug) if slug else slugify(title)
    return f"{date}-{name}-{session_id[-8:]}.md"


def human_date(ms: int | None) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def append_decision(cfg: Config, text: str, tags: list[str] | None = None) -> tuple[bool, str]:
    body = text.strip()
    if not body:
        return False, "empty decision"
    normalized = re.sub(r"\s+", " ", body)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    with state_lock(cfg):
        state = load_state(cfg)
        if digest in state["decisions"]:
            return False, "duplicate decision"
        state["decisions"][digest] = datetime.now().isoformat(timespec="seconds")
        save_state(cfg, state)
    title = body.splitlines()[0].strip()
    if len(title) > 90:
        title = title[:87].rstrip() + "..."
    rest = body[len(body.splitlines()[0]):].strip()
    tag_line = " ".join(f"#{tag.lstrip('#')}" for tag in (tags or []) if tag.strip())
    block = [f"## {datetime.now().strftime('%Y-%m-%d')} — {title}"]
    if rest:
        block.append("")
        block.append(rest)
    if tag_line:
        block.append("")
        block.append(tag_line)
    block.append("")
    existing = read_text(cfg.decisions_file)
    if not existing.strip():
        existing = "# Решения\n\n"
    write_text(cfg.decisions_file, existing.rstrip() + "\n\n" + "\n".join(block) + "\n")
    return True, title


def render_context(cfg: Config, state: dict) -> str:
    sessions = sorted(
        state["sessions"].items(),
        key=lambda item: (item[1].get("date", ""), item[1].get("exported_at", "")),
        reverse=True,
    )[:10]
    decisions = read_text(cfg.decisions_file)
    decision_lines = [line for line in decisions.splitlines() if line.startswith("## ")]
    decision_lines = decision_lines[::-1][:7]
    lines = [CONTEXT_START, f"_Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    lines.append("## Последние сессии")
    lines.append("")
    if sessions:
        lines.append("| Дата | Проект | Сессия |")
        lines.append("|---|---|---|")
        for _, meta in sessions:
            if not meta.get("file"):
                continue
            project = Path(meta.get("directory", "")).name or "?"
            title = (meta.get("title") or "session").replace("|", "\\|")
            lines.append(f"| {meta.get('date', '')} | {project} | [{title}](sessions/{meta['file']}) |")
    else:
        lines.append("_Пока нет экспортированных сессий._")
    lines.append("")
    lines.append("## Последние решения")
    lines.append("")
    if decision_lines:
        for line in decision_lines:
            lines.append(f"- {line.removeprefix('## ')}")
    else:
        lines.append("_Решений пока нет._")
    lines.append("")
    lines.append(CONTEXT_END)
    return "\n".join(lines)


def refresh_context(cfg: Config) -> Path:
    with state_lock(cfg):
        state = load_state(cfg)
        block = render_context(cfg, state)
        save_state(cfg, state)
    current = read_text(cfg.context_file)
    if not current.strip():
        current = CONTEXT_TEMPLATE
    if CONTEXT_START in current and CONTEXT_END in current:
        before = current.split(CONTEXT_START)[0]
        after = current.split(CONTEXT_END, 1)[1]
        updated = before + block + after
    else:
        updated = current.rstrip() + "\n\n" + block + "\n"
    write_text(cfg.context_file, updated)
    return cfg.context_file


def search(cfg: Config, query: str, limit: int = 20) -> list[dict]:
    needle = query.lower().strip()
    if not needle:
        return []
    results: list[dict] = []
    for path in sorted(cfg.ai_path.rglob("*.md")):
        if ".tmp" in path.name:
            continue
        text = read_text(path)
        for number, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                results.append(
                    {
                        "file": str(path.relative_to(cfg.ai_path)).replace("\\", "/"),
                        "line": number,
                        "text": line.strip()[:400],
                    }
                )
                if len(results) >= limit:
                    return results
    return results
