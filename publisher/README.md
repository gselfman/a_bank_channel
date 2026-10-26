# A-Bank Channel: публикация из GitHub без OpenAI API

## Архитектура
- Запланированная задача ChatGPT: 09:00, 14:00, 19:00 **Europe/Moscow**. Ищет и проверяет новости, формирует готовые JSON-посты в `posts/pending/`. Доступ на запись в GitHub из *автоматического* запуска пока НЕ подтверждён: необходимо дождаться первого фактического запуска и проверить наличие файлов.
- Ubuntu systemd timer: 09:30, 14:30, 19:30 **Europe/Moscow**. Забирает файлы из публичного GitHub и отправляет в Telegram.
- **Никаких вызовов OpenAI API** со стороны Ubuntu. Токен Telegram Bot API по-прежнему обязателен.
- Старый Docker worker (`compose.yaml`, `abank/`) не должен параллельно публиковать сообщения. Остановите именно worker, не удаляя `state/` и не сбрасывая настройки.

## Формат поста
Один JSON на одну публикацию: `posts/pending/2026-10-27-0900-example.json`.
```json
{
  "id": "2026-10-27-0900-example",
  "title": "Проверенный заголовок",
  "text": "Готовый текст поста. Источник: https://example.com/story",
  "source": "https://example.com/story",
  "publish_at": "2026-10-27T09:30:00+03:00",
  "image": "content/images/01.png",
  "status": "ready"
}
```
**Пример НЕ является реальной новостью, НЕ загружайте его в очередь.**
- `image` — обязательное поле: путь к уже загруженной в GitHub PNG/JPEG (например, `content/images/01.png`) либо прямой публичный HTTPS URL картинки; без изображения пост не будет опубликован.
- Для новых иллюстраций сначала физически загрузите файл в GitHub, затем укажите путь. **Автоматическая генерация и запись изображений через запланированную задачу ChatGPT не подтверждена.** Допустима подходящая существующая обложка из репозитория, но только если она соответствует новости.
- Бот отправляет текст до 1024 символов подписью к изображению (`sendPhoto`). При более длинном тексте отправляет отдельное изображение и отдельный текст (`sendMessage`).
- Учитывайте лимиты Telegram и права на использование изображений. Не используйте чужие фото без разрешения.
- Публикуются лишь `status: ready` с `publish_at` не позже времени запуска. SQLite предотвращает повторную отправку по `id`. Если Telegram успеет принять сообщение, а процесс аварийно завершится до записи в SQLite, остаётся небольшой риск дубля.
- Тестовый файл `automation/connection-test-2026-10-26.json` **никогда не публикуется**.

## Миграция существующего Ubuntu-сервера
Следующие команды учитывают существующий Docker-проект. Выполняйте из **каталога реально развёрнутого репозитория**, проверьте командой `pwd`:
```bash
cd /path/to/a_bank_channel
# Остановить СТАРЫЙ worker, не трогая другие контейнеры и данные
docker compose stop worker  # для старого Compose: docker-compose -f compose.yaml stop worker
git pull --ff-only origin main
# Убедиться, что нет второго старого cron или systemd publisher
systemctl list-timers --all | grep -i bank || true
crontab -l 2>/dev/null || true
```
Если ваш каталог `/opt/a_bank_channel`, используйте его. Если рабочая копия в другом месте, не создавайте вторую без необходимости.

Создайте env-файл для НОВОГО публикатора. **Существующий Telegram-токен нужно скопировать из существующей конфигурации локально на сервере — НЕ в чат и НЕ в GitHub**:
```bash
sudo install -m 600 /dev/null /etc/a-bank-publisher.env
sudo nano /etc/a-bank-publisher.env
```
```dotenv
TELEGRAM_BOT_TOKEN=your_existing_bot_token
TELEGRAM_CHAT_ID=@a_bank_wallet
GITHUB_REPO=gselfman/a_bank_channel
GITHUB_BRANCH=main
PUBLISHER_DB=/var/lib/a-bank-publisher/sent.sqlite3
```
**OPENAI_API_KEY не нужен**. Не отключайте старые задания, если не уверены, что именно они делают: сначала изучите их назначение.

```bash
# Создать отдельного системного пользователя, если его ещё нет
id a-bank >/dev/null 2>&1 || sudo adduser --system --group --no-create-home a-bank
sudo cp publisher/a-bank-publisher.service /etc/systemd/system/
sudo cp publisher/a-bank-publisher.timer /etc/systemd/system/
# В unit указан WorkDir /opt/a_bank_channel; если репозиторий расположен иначе,
# отредактируйте unit, заменив WorkingDirectory и ExecStart на реальные пути:
sudo systemctl edit --full a-bank-publisher.service
sudo systemctl daemon-reload
sudo systemctl enable --now a-bank-publisher.timer
systemctl list-timers --all a-bank-publisher.timer
sudo systemd-analyze calendar '*-*-* 09:30:00 Europe/Moscow'
```
**Не включайте сервис**, пока не проверены `/opt/a_bank_channel`, `/etc/a-bank-publisher.env`, доступ к Telegram и отсутствие второго публикатора. Для одиночного ручного запуска (он опубликует все созревшие `ready`-посты):
```bash
sudo systemctl start a-bank-publisher.service
sudo journalctl -u a-bank-publisher.service -n 80 --no-pager
```
Если постов в очереди нет, сервис ничего не отправит. **На сервере система не была развёрнута этой записью в GitHub.**

## Проверка первого полного цикла
1. После 09:00 проверить, записала ли запланированная задача файл в `posts/pending/`. Если нет — нет подтверждённой сквозной автоматизации; сервер ничего не сможет опубликовать.
2. Проверить `image`, `status=ready`, `publish_at`, ссылку на источник и корректность текста.
3. После 09:30 проверить Telegram и `journalctl -u a-bank-publisher.service`.
4. Повторный запуск не должен дублировать отправленный `id`.

## Условия эксплуатации
Система не использует OpenAI API. Ключ OpenAI не требуется. GitHub публичный по желанию владельца. Не хранить Telegram-токен, пароли и содержимое `.env` в репозитории.
