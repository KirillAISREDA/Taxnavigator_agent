# CC_SERVER_SETUP.md
# Промпт для Claude Code — полная настройка проекта на сервере
# 
# Использование:
# cd /opt/Taxnavigator_agent
# claude -p "$(cat CC_SERVER_SETUP.md)" --dangerously-skip-permissions
#
# Или в интерактивном режиме:
# claude --dangerously-skip-permissions
# > скопировать содержимое этого файла

Ты находишься в директории проекта TaxNavigator AI Agent на production-сервере.
Прочитай CLAUDE.md для контекста, затем выполни полную настройку и проверку.

## Задачи (выполняй последовательно, не останавливайся):

### 1. Проверь окружение
- Убедись что Docker и docker-compose установлены
- Покажи свободную RAM и диск: `free -h && df -h /`
- Покажи какие контейнеры уже запущены: `docker ps`

### 2. Создай .env
- Если .env не существует — скопируй из .env.example
- Сгенерируй APP_SECRET_KEY: `openssl rand -hex 32` и впиши его
- OPENAI_API_KEY — спроси у меня, НЕ придумывай
- Покажи мне итоговый .env (замаскируй ключи, покажи только первые 8 символов)

### 3. Настрой SSH для GitHub (если ещё не настроено)
- Проверь есть ли ~/.ssh/github_deploy
- Если нет — создай: `ssh-keygen -t ed25519 -C "taxnav-deploy" -f ~/.ssh/github_deploy -N ""`
- Добавь в ~/.ssh/config запись для github.com с этим ключом
- Проверь подключение: `ssh -T git@github.com`
- Если не работает — покажи мне публичный ключ для добавления в GitHub

### 4. Настрой git remote
- Проверь `git remote -v`
- Если remote нет — добавь: `git remote add origin git@github.com:KirillAISREDA/Taxnavigator_agent.git`
- Если remote через https — смени на ssh

### 5. Запусти Docker-стек
```bash
docker-compose up -d --build
```
- Дождись завершения сборки
- Покажи `docker-compose ps` — все контейнеры должны быть Up

### 6. Проверь здоровье сервисов
- Подожди 15 секунд после старта
- `curl -s http://localhost:8100/health | python3 -m json.tool`
- Если что-то не ok — покажи логи проблемного контейнера

### 7. Проверь каждый контейнер отдельно
```bash
docker-compose logs api --tail 20
docker-compose logs qdrant --tail 20
docker-compose logs redis --tail 20
docker-compose logs crawler --tail 20
```
- Если есть ошибки — попробуй починить и перезапустить

### 8. Тест чата
```bash
curl -s -X POST http://localhost:8100/api/chat/ \
  -H "Content-Type: application/json" \
  -d '{"message": "Hoe registreer ik een eenmanszaak?"}' | python3 -m json.tool
```
- Покажи полный ответ
- Если crawler ещё не закончил индексацию — это нормально, ответ может быть без RAG-контекста

### 9. Проверь доступность снаружи
- Покажи `curl -s http://localhost:8100/widget/ | head -5`
- Напомни мне открыть порт 8100 в файрволе если нужно:
  `ufw allow 8100/tcp` или аналог

### 10. Итоговый отчёт
Покажи сводку:
- Статус всех контейнеров
- Результат health check
- URL виджета: http://IP:8100/widget/
- Работает ли чат API
- Статус краулера (индексация идёт/завершена)
- Что осталось настроить (Telegram, WhatsApp, nginx, домен)

## Правила:
- НЕ останавливайся на ошибках — пробуй починить сам
- НЕ спрашивай подтверждения на каждый шаг (кроме OPENAI_API_KEY)
- Если контейнер падает — покажи лог и попробуй исправить
- Все команды выполняй из /opt/Taxnavigator_agent
