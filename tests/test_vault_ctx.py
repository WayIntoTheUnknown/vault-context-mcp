from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vault_ctx import sync
from vault_ctx.config import Config
from vault_ctx.digest import build_digest, estimate_tokens
from vault_ctx.redact import redact
from vault_ctx.sources import vault
from vault_ctx.sources.sqlite_db import DatabaseError, SessionRepository

SESSION_ID = "ses_test1234567890abcdef"
SECRET_USER = "use api key sk-abcdefghijklmnopqrstuvwxyz012345 for tests"


def make_db(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE session (
            id text PRIMARY KEY,
            project_id text NOT NULL DEFAULT 'p',
            workspace_id text,
            parent_id text,
            slug text NOT NULL,
            directory text NOT NULL,
            path text,
            title text NOT NULL,
            version text NOT NULL DEFAULT '0',
            share_url text,
            summary_additions integer,
            summary_deletions integer,
            summary_files integer,
            summary_diffs text,
            metadata text,
            cost real NOT NULL DEFAULT 0,
            tokens_input integer NOT NULL DEFAULT 0,
            tokens_output integer NOT NULL DEFAULT 0,
            tokens_reasoning integer NOT NULL DEFAULT 0,
            tokens_cache_read integer NOT NULL DEFAULT 0,
            tokens_cache_write integer NOT NULL DEFAULT 0,
            revert text,
            permission text,
            agent text,
            model text,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            time_compacting integer,
            time_archived integer
        );
        CREATE TABLE message (
            id text PRIMARY KEY,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        CREATE TABLE part (
            id text PRIMARY KEY,
            message_id text NOT NULL,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        """
    )
    connection.execute(
        """
        INSERT INTO session (id, slug, directory, title, time_created, time_updated,
                             tokens_input, tokens_output, cost)
        VALUES (?, 'brave-canyon', 'C:/work/project', 'Sync context with phone', 1700000000000, 1700000000000,
                1000, 500, 0.25)
        """,
        (SESSION_ID,),
    )
    messages = [
        ("msg_u1", 1700000000000, {"role": "user"}),
        (
            "msg_a1",
            1700000001000,
            {"role": "assistant", "model": {"providerID": "p", "modelID": "m"}},
        ),
    ]
    for message_id, created, data in messages:
        connection.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (message_id, SESSION_ID, created, created, json.dumps(data)),
        )
    parts = [
        ("prt_1", "msg_u1", {"type": "text", "text": SECRET_USER}),
        (
            "prt_2",
            "msg_a1",
            {"type": "text", "text": "Implemented the SQLite adapter and the vault sync path."},
        ),
        (
            "prt_3",
            "msg_a1",
            {
                "type": "tool",
                "tool": "read",
                "state": {
                    "status": "completed",
                    "title": "read config.py",
                    "input": {"filePath": "C:/work/project/config.py"},
                },
            },
        ),
        (
            "prt_4",
            "msg_a1",
            {
                "type": "tool",
                "tool": "bash",
                "state": {"status": "completed", "title": "pytest", "input": {"command": "pytest"}},
            },
        ),
        ("prt_5", "msg_a1", {"type": "reasoning", "text": "hidden reasoning"}),
    ]
    for part_id, message_id, data in parts:
        connection.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (part_id, message_id, SESSION_ID, 1700000000000, 1700000000000, json.dumps(data)),
        )
    connection.commit()
    connection.close()
    return path


@pytest.fixture()
def cfg(tmp_path: Path) -> Config:
    db = make_db(tmp_path / "opencode.db")
    config = Config(vault=tmp_path / "vault", ai_dir="ИИ", db=db, lang="en", max_tokens=2000)
    config.ensure_layout()
    return config


def test_redact_secrets() -> None:
    fake_bot = "123456789" + ":" + "AAH" + "dqTcvCH1vGWJxfSeofSAs0K5PALDsawX"
    text = (
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789 and Bearer abcdefghijklmnopqrstuvwxyz\n"
        "API_KEY=super-secret-value\n"
        f"bot {fake_bot}\n"
    )
    cleaned, count = redact(text)
    assert count >= 3
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in cleaned
    assert "super-secret-value" not in cleaned
    assert "dqTcvCH1vGWJxfSeofSAs0K5PALDsawX" not in cleaned


def test_digest_has_core_sections(cfg: Config) -> None:
    repo = SessionRepository(cfg.db)
    session = repo.resolve_session("last")
    messages = repo.messages(session.id)
    content, tokens = build_digest(cfg, session, messages, max_tokens=2000)
    assert "## Goal" in content
    assert "## Current state" in content
    assert "Implemented the SQLite adapter" in content
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in content
    assert tokens <= estimate_tokens(content) + 5
    assert len(content) <= 2000 * 4 * 1.5


def test_export_is_idempotent(cfg: Config) -> None:
    first = sync.export_session(cfg, identifier="last")
    assert first.status == "created"
    assert first.file and first.file.is_file()
    second = sync.export_session(cfg, identifier="last")
    assert second.status == "unchanged"
    assert len(list(cfg.sessions_dir.glob("*.md"))) == 1
    context = cfg.context_file.read_text(encoding="utf-8")
    assert "Sync context with phone" in context
    assert vault.CONTEXT_START in context and vault.CONTEXT_END in context


def test_force_rewrites(cfg: Config) -> None:
    sync.export_session(cfg, identifier="last")
    result = sync.export_session(cfg, identifier="last", force=True)
    assert result.status == "updated"


def test_decisions_dedupe(cfg: Config) -> None:
    added, _ = vault.append_decision(cfg, "Use SQLite read-only adapter", ["architecture"])
    assert added
    added_again, message = vault.append_decision(cfg, "Use SQLite read-only adapter", ["architecture"])
    assert not added_again
    assert "duplicate" in message
    text = cfg.decisions_file.read_text(encoding="utf-8")
    assert text.count("SQLite read-only adapter") == 1


def test_search(cfg: Config) -> None:
    sync.export_session(cfg, identifier="last")
    results = vault.search(cfg, "SQLite adapter")
    assert results and results[0]["file"].endswith(".md")


def test_handoff_without_frontmatter(cfg: Config) -> None:
    text = sync.handoff(cfg, identifier="last", max_tokens=800)
    assert not text.startswith("---")
    assert "## Goal" in text


def test_schema_guard(tmp_path: Path) -> None:
    db = tmp_path / "broken.db"
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE session (id text PRIMARY KEY)")
    connection.commit()
    connection.close()
    repo = SessionRepository(db)
    report = repo.check()
    assert report["schema_ok"] is False
    assert "message" in report["missing_tables"]
    with pytest.raises(DatabaseError):
        repo.list_sessions()


def test_missing_database(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError):
        SessionRepository(tmp_path / "nope.db").check()


def test_children_are_skipped(cfg: Config) -> None:
    connection = sqlite3.connect(cfg.db)
    connection.execute(
        "INSERT INTO session (id, slug, directory, title, parent_id, time_created, time_updated)"
        " VALUES ('ses_child', 'child', 'C:/work/project', 'child task', ?, 1700000002000, 1700000002000)",
        (SESSION_ID,),
    )
    connection.commit()
    connection.close()
    result = sync.export_session(cfg, identifier="ses_child")
    assert result.status == "skipped"
