# Установка на Ubuntu 20.04

Контейнер изолирует Python 3.12 от системного Python Ubuntu. Используйте существующую установку Docker; системное обновление ОС — отдельная задача. Для production поддерживайте ОС и Docker обновлёнными.

Если Docker/Compose нет, базовый вариант из пакетов Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose git python3
sudo systemctl enable --now docker
```

Команды ниже предполагают разрешённый доступ к Docker. При необходимости выполняйте Docker-команды через sudo. Участие пользователя в группе docker даёт практически root-доступ; не добавляйте туда посторонних.

```bash
git clone git@github.com:gselfman/a_bank_channel.git
cd a_bank_channel
python3 -m unittest discover -s tests -v
mkdir -p state
sudo chown 10001:10001 state
python3 deploy/configure_secrets.py
```

Владелец вводит токен и API-ключ в терминале без эха; значение не выводится и не попадает в history. Для начальной публикации OpenAI не требуется. `.env` имеет права 0600; не делать chmod 644 ради удобства. Compose читает `.env` на хосте, затем передаёт значения контейнеру.

Современный Compose:

```bash
docker compose build
docker compose run --rm worker python -m abank.main seed
docker compose run --rm worker python -m abank.main preflight
docker compose run --rm worker python -m abank.main publish-initial
```

На старом Compose заменить каждое `docker compose` на `docker-compose -f compose.yaml`. До запуска сервиса можно также использовать `bash deploy/channel COMMAND`, выбирающий доступный Compose.

После проверки текущего статуса пополнений:

```bash
bash deploy/channel confirm-status
bash deploy/channel resume
bash deploy/channel publish-initial --live
bash deploy/channel status
bash deploy/channel poll
docker compose up -d
docker compose logs --tail=50 worker
```

`publish-initial` без `--live` ничего не отправляет. Флаг --live отправляет только ранее согласованные seed-посты. При успехе навигация и статус закрепляются; `finalize` повторяет идемпотентные операции редактирования/закрепления.

Остановка/обновление без удаления состояния:

```bash
bash deploy/channel pause
docker compose stop
git pull --ff-only
docker compose build
docker compose up -d
bash deploy/channel resume
```

Не использовать `git reset --hard`, `docker system prune` и удаление state как способ исправления ошибок. Токен/ключ обновляют локально в `.env`, затем контейнер пересоздают: `docker compose up -d --force-recreate`.

Проверка worker: поле heartbeat в `status` должно обновляться примерно раз в 30 секунд (внешние запросы могут увеличить интервал). Ошибки источников видны в events; живой процесс не гарантирует успешного сбора. Docker перезапускает упавший worker, но не маскирует ошибки источника.
