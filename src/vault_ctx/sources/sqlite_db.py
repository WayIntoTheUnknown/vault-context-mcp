from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_COLUMNS = {
    "session": {
        "id",
        "title",
        "slug",
        "directory",
        "parent_id",
        "time_created",
        "time_updated",
        "tokens_input",
        "tokens_output",
        "cost",
        "time_archived",
    },
    "message": {"id", "session_id", "time_created", "data"},
    "part": {"id", "message_id", "session_id", "time_created", "data"},
}


class DatabaseError(RuntimeError):
    pass


@dataclass
class SessionInfo:
    id: str
    title: str
    slug: str
    directory: str
    parent_id: str | None
    time_created: int
    time_updated: int
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0
    summary_files: int | None = None
    summary_additions: int | None = None
    summary_deletions: int | None = None
    archived: bool = False

    @property
    def is_child(self) -> bool:
        return bool(self.parent_id)


@dataclass
class MessageInfo:
    id: str
    role: str
    created: int
    data: dict = field(default_factory=dict)
    parts: list[dict] = field(default_factory=list)

    @property
    def model(self) -> str | None:
        model = self.data.get("model") or {}
        provider = model.get("providerID")
        model_id = model.get("modelID")
        if provider and model_id:
            return f"{provider}/{model_id}"
        return model_id or provider


class SessionRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self):
        if not self.db_path.is_file():
            raise DatabaseError(f"opencode database not found: {self.db_path}")
        uri = f"{self.db_path.absolute().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.row_factory = sqlite3.Row
            yield connection
        finally:
            connection.close()

    def check(self) -> dict:
        with self._connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing_tables = sorted(set(REQUIRED_COLUMNS) - tables)
            missing_columns: dict[str, list[str]] = {}
            for table, columns in REQUIRED_COLUMNS.items():
                if table not in tables:
                    continue
                present = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
                missing = sorted(columns - present)
                if missing:
                    missing_columns[table] = missing
            counts = {}
            for table in REQUIRED_COLUMNS:
                if table in tables:
                    counts[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return {
            "db": str(self.db_path),
            "size_bytes": self.db_path.stat().st_size,
            "tables": sorted(tables),
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "counts": counts,
            "schema_ok": not missing_tables and not missing_columns,
        }

    def _require_schema(self, connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(set(REQUIRED_COLUMNS) - tables)
        if missing:
            raise DatabaseError(
                "unsupported opencode database schema, missing tables: " + ", ".join(missing)
            )

    def list_sessions(
        self,
        limit: int = 20,
        only_parents: bool = True,
        include_archived: bool = False,
        directory: str | None = None,
    ) -> list[SessionInfo]:
        clauses = []
        params: list[object] = []
        if only_parents:
            clauses.append("parent_id IS NULL")
        if not include_archived:
            clauses.append("time_archived IS NULL")
        if directory:
            clauses.append("directory = ?")
            params.append(directory)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT id, title, slug, directory, parent_id, time_created, time_updated,
                   tokens_input, tokens_output, cost, summary_files, summary_additions,
                   summary_deletions, time_archived
            FROM session {where}
            ORDER BY time_updated DESC
            LIMIT ?
        """
        params.append(max(1, limit))
        with self._connect() as connection:
            self._require_schema(connection)
            rows = connection.execute(query, params).fetchall()
        return [self._session_from_row(row) for row in rows]

    def get_session(self, session_id: str) -> SessionInfo:
        with self._connect() as connection:
            self._require_schema(connection)
            row = connection.execute(
                """
                SELECT id, title, slug, directory, parent_id, time_created, time_updated,
                       tokens_input, tokens_output, cost, summary_files, summary_additions,
                       summary_deletions, time_archived
                FROM session WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise DatabaseError(f"session not found: {session_id}")
        return self._session_from_row(row)

    def resolve_session(self, identifier: str | None) -> SessionInfo:
        if not identifier or identifier in {"last", "latest"}:
            sessions = self.list_sessions(limit=1)
            if not sessions:
                raise DatabaseError("no sessions found in the opencode database")
            return sessions[0]
        try:
            return self.get_session(identifier)
        except DatabaseError:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT id FROM session WHERE id LIKE ? ORDER BY time_updated DESC LIMIT 2",
                    (f"{identifier}%",),
                ).fetchall()
            if len(rows) == 1:
                return self.get_session(rows[0]["id"])
            if len(rows) > 1:
                raise DatabaseError(f"ambiguous session prefix: {identifier}") from None
            raise

    def messages(self, session_id: str) -> list[MessageInfo]:
        with self._connect() as connection:
            self._require_schema(connection)
            message_rows = connection.execute(
                "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
            part_rows = connection.execute(
                "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
        parts: dict[str, list[dict]] = {}
        for row in part_rows:
            try:
                payload = json.loads(row["data"])
            except json.JSONDecodeError:
                continue
            parts.setdefault(row["message_id"], []).append(payload)
        messages = []
        for row in message_rows:
            try:
                payload = json.loads(row["data"])
            except json.JSONDecodeError:
                payload = {}
            messages.append(
                MessageInfo(
                    id=row["id"],
                    role=payload.get("role", "unknown"),
                    created=row["time_created"],
                    data=payload,
                    parts=parts.get(row["id"], []),
                )
            )
        return messages

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> SessionInfo:
        return SessionInfo(
            id=row["id"],
            title=row["title"],
            slug=row["slug"],
            directory=row["directory"],
            parent_id=row["parent_id"],
            time_created=row["time_created"],
            time_updated=row["time_updated"],
            tokens_input=row["tokens_input"] or 0,
            tokens_output=row["tokens_output"] or 0,
            cost=row["cost"] or 0.0,
            summary_files=row["summary_files"],
            summary_additions=row["summary_additions"],
            summary_deletions=row["summary_deletions"],
            archived=bool(row["time_archived"]),
        )
