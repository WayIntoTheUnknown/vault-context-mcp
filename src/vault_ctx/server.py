from __future__ import annotations

import json
from pathlib import Path

from mcp.server import MCPServer

from . import sync
from .config import load_config
from .digest import build_digest, estimate_tokens
from .sources import vault as vault_store
from .sources.sqlite_db import DatabaseError, SessionRepository

INSTRUCTIONS = (
    "vault-context mirrors AI session context into a Markdown vault (Obsidian) and back. "
    "Use ctx_sync after finishing a task, ctx_digest when the user needs a portable handoff for "
    "another chat app, and ctx_append_decision to persist durable decisions. "
    "All files are UTF-8 Markdown; secrets are redacted automatically."
)

mcp = MCPServer(name="vault-context", instructions=INSTRUCTIONS)


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def ctx_sync(session_id: str | None = None, force: bool = False, max_tokens: int | None = None) -> str:
    """Export an opencode session (default: the most recent one) into the vault.

    Writes ИИ/sessions/<date>-<slug>-<id>.md, updates the auto block in ИИ/context.md
    and records a content hash so repeated calls are no-ops.
    """
    try:
        cfg = load_config()
        result = sync.export_session(
            cfg,
            identifier=session_id,
            force=force,
            max_tokens=max_tokens,
            refresh=True,
        )
        return _json(result.as_dict())
    except (DatabaseError, OSError) as error:
        return _json({"status": "error", "error": str(error)})


@mcp.tool()
def ctx_sync_all(limit: int = 20, force: bool = False) -> str:
    """Export the most recent opencode sessions (parent sessions only)."""
    try:
        cfg = load_config()
        results = sync.export_all(cfg, limit=max(1, min(limit, 500)), force=force)
        return _json(
            {
                "exported": [result.as_dict() for result in results],
                "counts": {
                    status: sum(1 for result in results if result.status == status)
                    for status in {result.status for result in results}
                },
            }
        )
    except (DatabaseError, OSError) as error:
        return _json({"status": "error", "error": str(error)})


@mcp.tool()
def ctx_digest(
    session_id: str | None = None,
    max_tokens: int | None = None,
    lang: str | None = None,
) -> str:
    """Build a compact handoff digest for pasting into another chat app (DeepSeek, ChatGPT, ...)."""
    try:
        cfg = load_config()
        text = sync.handoff(cfg, identifier=session_id, max_tokens=max_tokens, lang=lang)
        return text
    except (DatabaseError, OSError) as error:
        return _json({"status": "error", "error": str(error)})


@mcp.tool()
def ctx_get_context(project: str | None = None) -> str:
    """Return the current vault context file (ИИ/context.md)."""
    cfg = load_config()
    text = vault_store.read_text(cfg.context_file, "")
    if not text:
        return _json({"status": "missing", "file": str(cfg.context_file)})
    return text


@mcp.tool()
def ctx_append_decision(text: str, tags: list[str] | None = None) -> str:
    """Append a durable decision to ИИ/decisions.md (deduplicated by content)."""
    cfg = load_config()
    cfg.ensure_layout()
    added, message = vault_store.append_decision(cfg, text, tags)
    if added:
        vault_store.refresh_context(cfg)
    return _json({"added": added, "message": message, "file": str(cfg.decisions_file)})


@mcp.tool()
def ctx_search(query: str, limit: int = 10) -> str:
    """Search exported sessions, context and decisions inside the vault."""
    cfg = load_config()
    cfg.ensure_layout()
    results = vault_store.search(cfg, query, limit=max(1, min(limit, 100)))
    return _json({"query": query, "count": len(results), "results": results})


@mcp.tool()
def ctx_status() -> str:
    """Report vault, database and export state (paths, schema compatibility, counts)."""
    return _json(sync.status(load_config()))


@mcp.resource("ctx://context", mime_type="text/markdown")
def context_resource() -> str:
    """Current vault context file."""
    cfg = load_config()
    return vault_store.read_text(cfg.context_file, "")


@mcp.resource("ctx://decisions", mime_type="text/markdown")
def decisions_resource() -> str:
    """Decision log."""
    cfg = load_config()
    return vault_store.read_text(cfg.decisions_file, "")


@mcp.resource("ctx://status", mime_type="application/json")
def status_resource() -> str:
    """Vault and database status as JSON."""
    return _json(sync.status(load_config()))


@mcp.resource("ctx://session/{session_id}", mime_type="text/markdown")
def session_resource(session_id: str) -> str:
    """Rendered digest of a stored session."""
    cfg = load_config()
    repo = SessionRepository(cfg.db)
    session = repo.resolve_session(session_id)
    content, _ = build_digest(cfg, session, repo.messages(session.id))
    return content


@mcp.prompt()
def handoff(session_id: str = "last", max_tokens: int = 3000) -> str:
    """Prepare a portable handoff digest of a session for another AI chat."""
    cfg = load_config()
    try:
        text = sync.handoff(cfg, identifier=session_id, max_tokens=max_tokens)
    except DatabaseError as error:
        return f"Failed to build handoff: {error}"
    estimated = estimate_tokens(text)
    return f"{text}\n\n---\n_Estimated tokens: {estimated}. Vault: {Path(cfg.ai_path)}_"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
