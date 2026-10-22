from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import sync
from .config import Config, load_config
from .sources import vault
from .sources.sqlite_db import DatabaseError


def _setup_logging(cfg: Config, quiet: bool) -> None:
    logger = logging.getLogger("vault_ctx")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    try:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass
    if not quiet:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_init(cfg: Config, args) -> int:
    cfg.ensure_layout()
    if not cfg.context_file.is_file():
        vault.write_text(cfg.context_file, vault.CONTEXT_TEMPLATE)
    if not cfg.decisions_file.is_file():
        vault.write_text(cfg.decisions_file, "# Решения\n")
    vault.refresh_context(cfg)
    _dump(
        {
            "status": "ok",
            "ai_dir": str(cfg.ai_path),
            "context": str(cfg.context_file),
            "decisions": str(cfg.decisions_file),
            "sessions": str(cfg.sessions_dir),
        }
    )
    return 0


def _cmd_export(cfg: Config, args) -> int:
    try:
        if args.all:
            results = sync.export_all(
                cfg,
                limit=args.limit,
                force=args.force,
                dry_run=args.dry_run,
                lang=args.lang,
                max_tokens=args.max_tokens,
            )
            payload = [result.as_dict() for result in results]
        else:
            result = sync.export_session(
                cfg,
                identifier=args.session,
                force=args.force,
                dry_run=args.dry_run,
                lang=args.lang,
                max_tokens=args.max_tokens,
            )
            payload = result.as_dict()
    except DatabaseError as error:
        _dump({"status": "error", "error": str(error)})
        return 2
    if args.json:
        _dump(payload)
    else:
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            print(f"{item['status']:9} {item['session_id']}  {item.get('file') or item.get('message')}")
    return 0


def _cmd_digest(cfg: Config, args) -> int:
    try:
        text = sync.handoff(
            cfg, identifier=args.session, max_tokens=args.max_tokens, lang=args.lang
        )
    except DatabaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.out:
        vault.write_text(Path(args.out).expanduser(), text)
        print(str(Path(args.out).expanduser()))
    else:
        print(text)
    return 0


def _cmd_decisions(cfg: Config, args) -> int:
    if args.action == "add":
        cfg.ensure_layout()
        added, message = vault.append_decision(cfg, args.text, args.tag)
        if added:
            vault.refresh_context(cfg)
        _dump({"added": added, "message": message})
        return 0 if added else 1
    text = vault.read_text(cfg.decisions_file)
    print(text)
    return 0


def _cmd_context(cfg: Config, args) -> int:
    if args.action == "refresh":
        cfg.ensure_layout()
        print(str(vault.refresh_context(cfg)))
        return 0
    print(vault.read_text(cfg.context_file))
    return 0


def _cmd_search(cfg: Config, args) -> int:
    cfg.ensure_layout()
    _dump(vault.search(cfg, args.query, limit=args.limit))
    return 0


def _cmd_doctor(cfg: Config, args) -> int:
    _dump(sync.status(cfg))
    return 0


def _cmd_serve(cfg: Config, args) -> int:
    from .server import mcp

    mcp.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault-ctx",
        description="Mirror AI session context into a Markdown vault",
    )
    parser.add_argument("--vault", help="vault root (default: VAULT_PATH or config default)")
    parser.add_argument("--ai-dir", help="AI folder inside the vault (default: ИИ)")
    parser.add_argument("--db", help="opencode database path")
    parser.add_argument("--lang", choices=["en", "ru"], help="digest language")
    parser.add_argument("--max-tokens", type=int, help="digest token budget")
    parser.add_argument("--quiet", action="store_true", help="log to file only")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the MCP server over stdio")
    serve.set_defaults(func=_cmd_serve)

    init = sub.add_parser("init", help="create ИИ/ layout and templates")
    init.set_defaults(func=_cmd_init)

    export = sub.add_parser("export", help="export session(s) into the vault")
    export.add_argument("--session", help="session id or prefix (default: latest)")
    export.add_argument("--all", action="store_true", help="export many recent sessions")
    export.add_argument("--limit", type=int, default=20)
    export.add_argument("--force", action="store_true")
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("--json", action="store_true")
    export.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    export.add_argument("--lang", choices=["en", "ru"], default=argparse.SUPPRESS)
    export.add_argument("--max-tokens", type=int, default=argparse.SUPPRESS)
    export.set_defaults(func=_cmd_export)

    digest = sub.add_parser("digest", help="print a portable handoff digest")
    digest.add_argument("--session")
    digest.add_argument("--out")
    digest.add_argument("--lang", choices=["en", "ru"], default=argparse.SUPPRESS)
    digest.add_argument("--max-tokens", type=int, default=argparse.SUPPRESS)
    digest.set_defaults(func=_cmd_digest)

    decisions = sub.add_parser("decisions", help="manage the decision log")
    decisions.add_argument("action", choices=["add", "list"])
    decisions.add_argument("text", nargs="?", default="")
    decisions.add_argument("--tag", action="append", default=[])
    decisions.set_defaults(func=_cmd_decisions)

    context = sub.add_parser("context", help="print or refresh context.md")
    context.add_argument("action", choices=["show", "refresh"], nargs="?", default="show")
    context.set_defaults(func=_cmd_context)

    search = sub.add_parser("search", help="search the AI folder")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(func=_cmd_search)

    doctor = sub.add_parser("doctor", help="check paths, database schema and export state")
    doctor.set_defaults(func=_cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    cfg = load_config(
        vault=args.vault,
        ai_dir=args.ai_dir,
        db=args.db,
        lang=args.lang,
        max_tokens=args.max_tokens,
    )
    _setup_logging(cfg, args.quiet)
    try:
        return args.func(cfg, args)
    except DatabaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
