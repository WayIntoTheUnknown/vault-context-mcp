# Android: синхронизация vault без серверов

Схема: ПК ⇄ телефон напрямую (Syncthing, P2P), без облачной подписки и без своего сервера.

## 1. ПК

1. Установи Syncthing: `winget install --id Syncthing.Syncthing` (или с syncthing.net).
2. Открой `http://127.0.0.1:8384`, добавь папку:
   - Folder ID: `obsidian-ai`
   - Path: `C:\Obsidian\Система мобильная`
   - Folder Type: Send & Receive
3. Вкладка Actions → Show ID — понадобится для телефона.

## 2. Телефон

1. Поставь **Syncthing-Fork** (F-Droid, не из Play Store — версия из Play устарела).
2. Добавь устройство по ID с ПК, подтверди на ПК.
3. Прими общую папку, путь: `Internal storage/Obsidian/AI-vault`.
4. В Syncthing-Fork включи автозапуск и работу в фоне (отключи оптимизацию батареи для приложения).

## 3. Obsidian Mobile

1. Obsidian → Open folder as vault → выбрать папку из п.2.
2. Открыть `ИИ/context.md`, скопировать дайджест и вставить в DeepSeek.
3. Для быстрого доступа добавь `ИИ/context.md` в закладки/на главный экран.

## 4. .stignore (обязательно)

В корне vault создай `.stignore`:

```
.obsidian/workspace*
.obsidian/cache
.trash
ИИ/.state.json
ИИ/.state.lock
*.tmp
*.sync-conflict-*
.sync/
```

## 5. Фолбэк без Obsidian Mobile

Дайджест можно получать в Telegram: `vault-ctx digest --session last --out handoff.md` или через существующий бот командой `/ctx`.
Приложение DeepSeek файлы не читает — текст копируется вручную.

## 6. Если конфликты

- Правило: vault пишет только ПК (или только телефон в момент правки), не оба одновременно.
- Конфликтные файлы `*.sync-conflict-*` можно удалять: автоблок `context.md` перегенерируется.
