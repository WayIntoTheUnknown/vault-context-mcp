from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .digest import build_digest, build_handoff, session_display_title
from .sources import vault
from .sources.sqlite_db import DatabaseError, SessionInfo, SessionRepository

logger = logging.getLogger("vault_ctx")


@dataclass
class ExportResult:
    status: str
    session_id: str
    title: str
    file: Path | None = None
    tokens: int = 0
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "title": self.title,
            "file": str(self.file) if self.file else None,
            "tokens": self.tokens,
            "message": self.message,
        }


def _repo(cfg: Config) -> SessionRepository:
    return SessionRepository(cfg.db)


def export_session(
    cfg: Config,
    identifier: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    lang: str | None = None,
    max_tokens: int | None = None,
    refresh: bool = True,
) -> ExportResult:
    repository = _repo(cfg)
    session = repository.resolve_session(identifier)
    return export_known_session(
        cfg,
        session,
        repository,
        force=force,
        dry_run=dry_run,
        lang=lang,
        max_tokens=max_tokens,
        refresh=refresh,
    )


def export_known_session(
    cfg: Config,
    session: SessionInfo,
    repository: SessionRepository,
    force: bool = False,
    dry_run: bool = False,
    lang: str | None = None,
    max_tokens: int | None = None,
    refresh: bool = True,
) -> ExportResult:
    if session.is_child and not cfg.include_children:
        return ExportResult("skipped", session.id, session.title, message="child session")

    messages = repository.messages(session.id)
    if not messages:
        return ExportResult("skipped", session.id, session.title, message="empty session")

    title = session_display_title(cfg, session, messages)
    content, tokens = build_digest(cfg, session, messages, max_tokens=max_tokens, lang=lang)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    filename = vault.session_filename(title, session.slug, session.id, session.time_created)
    target = cfg.sessions_dir / filename

    state = vault.load_state(cfg)
    previous = state["sessions"].get(session.id) or {}
    if not force and previous.get("hash") == digest and target.is_file():
        return ExportResult("unchanged", session.id, title, target, tokens)

    if dry_run:
        return ExportResult("dry-run", session.id, title, target, tokens)

    cfg.ensure_layout()
    vault.write_text(target, content)
    state = vault.load_state(cfg)
    state["sessions"][session.id] = {
        "file": filename,
        "hash": digest,
        "tokens": tokens,
        "title": title,
        "date": datetime.fromtimestamp(session.time_created / 1000).strftime("%Y-%m-%d"),
        "directory": session.directory,
        "messages": len(messages),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    if previous.get("file") and previous.get("file") != filename:
        stale = cfg.sessions_dir / previous["file"]
        try:
            stale.unlink()
        except OSError:
            pass
    vault.save_state(cfg, state)
    if refresh:
        vault.refresh_context(cfg)
    status = "updated" if previous.get("hash") else "created"
    return ExportResult(status, session.id, title, target, tokens)


def export_all(
    cfg: Config,
    limit: int = 50,
    force: bool = False,
    dry_run: bool = False,
    lang: str | None = None,
    max_tokens: int | None = None,
) -> list[ExportResult]:
    repository = _repo(cfg)
    sessions = repository.list_sessions(limit=limit)
    results = []
    for session in sessions:
        try:
            results.append(
                export_known_session(
                    cfg,
                    session,
                    repository,
                    force=force,
                    dry_run=dry_run,
                    lang=lang,
                    max_tokens=max_tokens,
                    refresh=False,
                )
            )
        except Exception as error:
            logger.exception("failed to export session %s", session.id)
            results.append(ExportResult("error", session.id, session.title, message=str(error)))
    if results and not dry_run:
        vault.refresh_context(cfg)
    return results


def handoff(
    cfg: Config,
    identifier: str | None = None,
    max_tokens: int | None = None,
    lang: str | None = None,
) -> str:
    repository = _repo(cfg)
    session = repository.resolve_session(identifier)
    messages = repository.messages(session.id)
    return build_handoff(cfg, session, messages, max_tokens=max_tokens, lang=lang)


def status(cfg: Config) -> dict:
    info = {
        "vault": str(cfg.vault),
        "ai_dir": str(cfg.ai_path),
        "db": str(cfg.db),
        "lang": cfg.lang,
        "max_tokens": cfg.max_tokens,
        "vault_exists": cfg.vault.is_dir(),
        "ai_dir_exists": cfg.ai_path.is_dir(),
        "db_exists": cfg.db.is_file(),
    }
    try:
        info["database"] = _repo(cfg).check()
    except DatabaseError as error:
        info["database"] = {"error": str(error)}
    state = vault.load_state(cfg)
    info["exported_sessions"] = len([meta for meta in state["sessions"].values() if meta.get("file")])
    info["decisions"] = len(state["decisions"])
    info["last_export"] = max(
        (meta.get("exported_at", "") for meta in state["sessions"].values()), default=""
    )
    return info
