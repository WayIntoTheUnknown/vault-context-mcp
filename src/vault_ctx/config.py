from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VAULT = r"C:\Obsidian\Система мобильная"
DEFAULT_AI_DIR = "ИИ"
DEFAULT_LANG = "en"
DEFAULT_MAX_TOKENS = 3000


def default_vault_path() -> Path:
    value = os.environ.get("VAULT_PATH") or DEFAULT_VAULT
    return Path(value).expanduser()


def default_ai_dir() -> str:
    return os.environ.get("VAULT_CTX_AI_DIR") or DEFAULT_AI_DIR


def default_lang() -> str:
    return (os.environ.get("VAULT_CTX_LANG") or DEFAULT_LANG).lower()


def default_max_tokens() -> int:
    raw = os.environ.get("VAULT_CTX_MAX_TOKENS")
    try:
        return max(500, int(raw)) if raw else DEFAULT_MAX_TOKENS
    except ValueError:
        return DEFAULT_MAX_TOKENS


def db_candidates() -> list[Path]:
    candidates = [
        Path.home() / ".local" / "share" / "opencode" / "opencode.db",
        Path(os.environ.get("APPDATA", "")) / "opencode" / "opencode.db",
        Path(os.environ.get("LOCALAPPDATA", "")) / "opencode" / "opencode.db",
        Path(os.environ.get("XDG_DATA_HOME", "")) / "opencode" / "opencode.db",
    ]
    return [path for path in candidates if len(str(path)) > 0 and str(path) not in {".", ""}]


def default_db_path() -> Path:
    explicit = os.environ.get("OPENCODE_DB")
    if explicit:
        return Path(explicit).expanduser()
    for candidate in db_candidates():
        if candidate.is_file():
            return candidate
    return db_candidates()[0]


@dataclass(frozen=True)
class Config:
    vault: Path = field(default_factory=default_vault_path)
    ai_dir: str = field(default_factory=default_ai_dir)
    db: Path = field(default_factory=default_db_path)
    lang: str = field(default_factory=default_lang)
    max_tokens: int = field(default_factory=default_max_tokens)
    include_children: bool = False

    @property
    def ai_path(self) -> Path:
        return self.vault / self.ai_dir

    @property
    def sessions_dir(self) -> Path:
        return self.ai_path / "sessions"

    @property
    def archive_dir(self) -> Path:
        return self.ai_path / "archive"

    @property
    def context_file(self) -> Path:
        return self.ai_path / "context.md"

    @property
    def decisions_file(self) -> Path:
        return self.ai_path / "decisions.md"

    @property
    def state_file(self) -> Path:
        return self.ai_path / ".state.json"

    @property
    def log_file(self) -> Path:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
        return Path(base) / "vault-ctx" / "vault-ctx.log"

    def ensure_layout(self) -> None:
        for path in (self.ai_path, self.sessions_dir, self.archive_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_config(**overrides) -> Config:
    values = {key: value for key, value in overrides.items() if value is not None}
    return Config(**values)
