# vault-context-mcp

MCP-сервер и плагин opencode, которые зеркалят контекст AI-сессий в Markdown-vault (Obsidian),
чтобы продолжать работу на телефоне и переносить контекст в другие чат-приложения.

Серверов не требует: всё работает локально, синхронизация vault — P2P (Syncthing) или любым
файловым синком. MCP-транспорт — stdio.

## Что делает

```
opencode.db (read-only) ──► дайджест сессии ──► ИИ/sessions/*.md
                                  │                  │
                                  └──► ИИ/context.md ◄┘
                                  └──► ИИ/decisions.md
```

- читает сессии opencode напрямую из SQLite (desktop и CLI), без вызова `opencode`;
- собирает компактный handoff-дайджест (цель, состояние, решения, файлы, последние сообщения);
- вырезает секреты (`sk-`, `ghp_`, `Bearer`, `TOKEN=`, приватные ключи, токены Telegram и т.п.);
- идемпотентен: повторный экспорт по хэшу не перезаписывает файл;
- обновляет авто-блок в `context.md`, сохраняя ручные правки вокруг маркеров.

## Установка

```bash
uvx --from git+https://github.com/WayIntoTheUnknown/vault-context-mcp vault-ctx serve
# или
pip install git+https://github.com/WayIntoTheUnknown/vault-context-mcp
```

Из исходников:

```bash
git clone https://github.com/WayIntoTheUnknown/vault-context-mcp
cd vault-context-mcp
python -m venv .venv
.venv\Scripts\pip install -e .        # Windows
# .venv/bin/pip install -e .          # Linux/macOS
```

## Быстрый старт

```bash
vault-ctx init                 # создать ИИ/ в vault
vault-ctx doctor               # проверить пути, схему БД, счётчики
vault-ctx export               # последняя сессия → ИИ/sessions/
vault-ctx export --all --limit 20
vault-ctx digest --out handoff.md   # компактный дайджест для DeepSeek/ChatGPT
vault-ctx decisions add "Перешли на SQLite-адаптер" --tag architecture
```

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `VAULT_PATH` | `C:\Obsidian\Система мобильная` | корень vault |
| `VAULT_CTX_AI_DIR` | `ИИ` | папка для контекста |
| `OPENCODE_DB` | `~/.local/share/opencode/opencode.db` | путь к БД opencode |
| `VAULT_CTX_LANG` | `en` | язык дайджеста (`en`/`ru`) |
| `VAULT_CTX_MAX_TOKENS` | `3000` | бюджет дайджеста |
| `VAULT_CTX_PYTHON` | `python` | интерпретатор для плагина opencode |
| `VAULT_CTX_DISABLE` | — | `1` отключает авто-экспорт |

## Подключение к opencode

`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "vault-context": {
      "type": "local",
      "command": ["C:\\path\\to\\.venv\\Scripts\\python.exe", "-m", "vault_ctx", "serve"],
      "environment": {
        "VAULT_PATH": "C:\\Obsidian\\Система мобильная",
        "VAULT_CTX_AI_DIR": "ИИ"
      },
      "enabled": true
    }
  }
}
```

Инструменты: `ctx_sync`, `ctx_sync_all`, `ctx_digest`, `ctx_get_context`, `ctx_append_decision`,
`ctx_search`, `ctx_status`. Ресурсы: `ctx://context`, `ctx://decisions`, `ctx://status`,
`ctx://session/{id}`. Промпт: `handoff`.

## Подключение к другим клиентам

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "vault-context": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/WayIntoTheUnknown/vault-context-mcp", "vault-ctx", "serve"],
      "env": { "VAULT_PATH": "C:/Obsidian/Система мобильная" }
    }
  }
}
```

Cursor, Cline, Zed и любой MCP-клиент — тот же stdio-командой.

## Авто-экспорт в opencode

Скопируй `plugin/opencode-vault-context.ts` в `~/.config/opencode/plugins/` (или
`.opencode/plugins/` проекта). Плагин слушает `session.idle`, делает debounce 3 с и вызывает
`vault-ctx export --session <id> --quiet`. Ошибки пишутся в
`%LOCALAPPDATA%\vault-ctx\vault-ctx.log` и не влияют на агента.

Путь к Python задаётся `VAULT_CTX_PYTHON` (например,
`C:\...\vault-context-mcp\.venv\Scripts\python.exe`).

## Vault-протокол

```
ИИ/
  context.md     # авто-блок между маркерами + ручные правки
  decisions.md   # append-only лог решений
  sessions/      # дайджесты сессий
  archive/
  .state.json    # хэши/курсоры (не синхронизировать)
```

Правило для агента (в `AGENTS.md`): перед завершением задачи дописать решение в
`decisions.md` и обновить `context.md`, не раздувая его сверх 200 строк.

## Телефон (Android)

1. Syncthing на ПК + Syncthing-Fork (F-Droid) на телефоне, общая папка vault.
2. `.stignore`: `.obsidian/workspace*`, `.trash`, `ИИ/.state.json`, `*.sync-conflict-*`, `*.lock`.
3. Obsidian Mobile → `ИИ/context.md` → копируешь дайджест в приложение DeepSeek.
4. Без Obsidian: `vault-ctx digest` в Telegram-боте (`/ctx`) как fallback.

## Ограничения

- приложение DeepSeek не читает файлы и не отдаёт историю чатов — контекст переносится вставкой
  (или отдельным мостом через DeepSeek API);
- схема БД opencode может меняться: `vault-ctx doctor` покажет несовместимость, адаптер читает только
  `session`/`message`/`part`;
- БД большая (сотни МБ): запросы только read-only по индексам, без `VACUUM`.

## Разработка

```bash
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check .
```

MIT License.

