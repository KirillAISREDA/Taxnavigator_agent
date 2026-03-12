# Настройка автодеплоя через GitHub Actions + SSH

## Что происходит
```
git push → GitHub Actions → SSH на сервер → git pull → docker-compose up → health check
```

Всё работает в облаке GitHub. На сервере ничего дополнительного не нужно.

---

## Шаг 1: Deploy Key (уже сделано ✅)

SSH-ключ для git pull уже настроен.

---

## Шаг 2: Добавить Secrets в GitHub

Открой: **Settings → Secrets and variables → Actions → New repository secret**

Добавь 4 секрета:

| Name | Value | Пример |
|------|-------|--------|
| `SERVER_HOST` | IP-адрес сервера | `185.xxx.xxx.xxx` |
| `SERVER_USER` | Имя пользователя SSH | `root` |
| `SERVER_PORT` | SSH порт | `22` |
| `SERVER_SSH_KEY` | Приватный SSH-ключ (см. ниже) | содержимое файла |

### Как получить SERVER_SSH_KEY:

На сервере выполни:
```bash
cat ~/.ssh/id_ed25519
```
или если нет ключа:
```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519
```

Скопируй ВСЁ содержимое (включая строки BEGIN и END):
```
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG...
...
-----END OPENSSH PRIVATE KEY-----
```

И вставь в **GitHub → Settings → Secrets → SERVER_SSH_KEY**

⚠️ Если ты заходишь на сервер по паролю, нужно:
```bash
# На сервере: разрешить авторизацию по ключу
# (обычно уже включено)
cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

---

## Шаг 3: Запушить workflow

Файл `.github/workflows/deploy.yml` уже в проекте.
Просто запушь его в main:

```bash
cd /opt/Taxnavigator_agent
git add .github/workflows/deploy.yml
git commit -m "Add auto-deploy workflow"
git push origin main
```

Этот пуш сам запустит первый деплой!

---

## Шаг 4: Проверить

1. Открой **GitHub → Actions** (вкладка наверху)
2. Увидишь workflow "Deploy to Server"
3. Зелёная галочка = деплой успешен
4. Красный крестик = кликни, посмотри лог ошибки

---

## Как это выглядит в работе

После настройки каждый `git push` в `main`:

1. GitHub Actions запускает job (~10 сек на старт)
2. Подключается к серверу по SSH
3. Выполняет `git pull`
4. Если изменились Dockerfile/requirements → полный rebuild
5. Если только код → быстрый рестарт API
6. Health check
7. Готово (~30-60 сек от push до деплоя)

---

## Мониторинг

- **GitHub:** вкладка Actions — история всех деплоев с логами
- **Сервер:** `docker-compose ps` и `docker-compose logs -f`

---

## FAQ

**Q: Можно деплоить только по тегу, а не на каждый push?**
Замени в deploy.yml:
```yaml
on:
  push:
    tags: ['v*']
```

**Q: Как сделать деплой вручную из GitHub?**
Добавь в deploy.yml:
```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:  # ← эта строка
```
Появится кнопка "Run workflow" во вкладке Actions.
