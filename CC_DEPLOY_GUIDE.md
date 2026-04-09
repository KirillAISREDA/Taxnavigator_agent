# Инструкция по запуску через Claude Code (CC)

## 1. Установка Claude Code (если ещё нет)

```bash
npm install -g @anthropic-ai/claude-code
```

## 2. Подключение к серверу

SSH на сервер, перейди в директорию проекта:

```bash
ssh root@your-server-ip
cd /opt/Taxnavigator_agent
```

## 3. Запуск Claude Code

```bash
claude
```

CC автоматически прочитает `CLAUDE.md` и поймёт контекст проекта.

## 4. Команда для полного деплоя БЕЗ подтверждений

Чтобы CC не спрашивал разрешение на каждую команду,
запусти с флагом `--dangerously-skip-permissions`:

```bash
claude --dangerously-skip-permissions
```

Или для одноразового выполнения конкретной задачи:

```bash
claude -p "Разверни проект: создай .env из .env.example, запусти docker-compose up -d --build, проверь health endpoint" --dangerously-skip-permissions
```

## 5. Полезные промпты для CC

### Первичный деплой:
```
Разверни проект на этом сервере:
1. Сгенерируй SSH-ключ для GitHub deploy key
2. Создай .env из .env.example (спроси у меня OPENAI_API_KEY)
3. Запусти docker-compose up -d --build
4. Дождись старта всех контейнеров
5. Проверь /health endpoint
6. Покажи мне итоговый статус
```

### Обновление после изменений:
```
Обнови проект: git pull, пересобери изменённые контейнеры, проверь health
```

### Диагностика проблем:
```
Проверь статус всех сервисов: docker-compose ps, логи каждого контейнера, health endpoint
```

### Добавить новый источник знаний:
```
Добавь новый источник в config/sources.json: [URL], категория [X], перезапусти crawler
```

## 6. Файл .claude/settings.json

Уже включён в проект. Разрешает CC выполнять:
- git, docker, docker-compose команды
- чтение/запись файлов
- curl, pip, python
- systemctl (для системных сервисов)

Без `--dangerously-skip-permissions` CC будет спрашивать
подтверждение только на команды НЕ из этого списка.

## 7. Альтернатива: разрешить ВСЁ через настройки

Если не хочешь использовать --dangerously-skip-permissions каждый раз,
можно один раз настроить в CC:

```bash
claude config set permissions.allow "Bash(*)" "Read(*)" "Write(*)"
```

Это разрешит CC выполнять любые bash-команды без подтверждений навсегда.
Используй только на своём сервере, не на продакшене с чужими данными.
