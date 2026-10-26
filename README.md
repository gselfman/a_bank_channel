# A-Bank Channel

Сервис для Telegram-канала АБанка: утверждённый начальный комплект из **35 текстов и 35 обложек**, мониторинг публичной ленты Алтына и сайтов, подготовка новых материалов и изображений через OpenAI API, очередь и расписание публикаций.

**Агенту установки начать с [docs/DEPLOY_AGENT_PROMPT.md](docs/DEPLOY_AGENT_PROMPT.md).** Там готовое задание с учётом разрешения владельца на первичную публикацию.

## Что внутри

| Путь | Назначение |
|---|---|
| content/posts.json, content/images/ | Согласованные тексты, HTML-подписи и изображения |
| content/review.html | Просмотр комплекта без публикации |
| brand/logo.png | Предоставленный фирменный логотип |
| abank/ | Сбор, ИИ, SQLite, Telegram, CLI и worker |
| prompts/ | Правила переписывания и визуальный стиль |
| config.json, .env.example | Несекретные настройки и шаблон переменных |
| deploy/, Dockerfile, compose.yaml | Установка и запуск на Ubuntu через Python 3.12 в контейнере |
| tests/ | Тесты без доступа к реальному Telegram/OpenAI |

Мониторинг `https://t.me/s/altynwallet` не требует коннектора или Telegram-авторизации. Это парсинг публичной HTML-страницы; доступ и разметку нужно проверить на сервере. Публикация выполняется другим путём — Telegram Bot API, бот должен быть администратором @a_bank_wallet.

Сбор → сверка с текущими сайтами → ИИ-черновик с цитатами → обложка с точным логотипом → ручное одобрение → расписание → Telegram. Новый материал не публикуется только на основании решения модели. Первые 35 уже согласованы и доступны для пакетной отправки.

## Быстрый старт

1. Следовать [установке Ubuntu](docs/DEPLOY_UBUNTU.md).
2. Внести секреты скрытым вводом на сервере: `python3 deploy/configure_secrets.py`.
3. `bash deploy/channel seed`, `preflight`, `publish-initial` (dry run).
4. Проверить актуальность статуса СБП, затем `confirm-status`, `resume`, `publish-initial --live`.
5. `poll`, затем запустить контейнер worker.

По умолчанию нет расходов API: AI выключен, бюджет нулевой, будущее расписание выключено. Владелец ещё не задал бюджет/частоту; установщик должен получить эти настройки, не выбрать их молча. Первичный комплект не требует OpenAI API.

Тесты: `python3 -m unittest discover -s tests -v`. Core использует стандартную библиотеку; Pillow нужен только для будущих обложек.

## Документация

- [Задание агенту установки](docs/DEPLOY_AGENT_PROMPT.md)
- [Ubuntu и Docker](docs/DEPLOY_UBUNTU.md)
- [Управление, ошибки, backup](docs/OPERATIONS.md)
- [Редакционные правила](docs/EDITORIAL_RULES.md)
- [Проверенные границы и ограничения](docs/KNOWN_LIMITATIONS.md)

Текущий статус поставки: код и локальные тесты; реальные Telegram/OpenAI/серверные проверки выполняются при развёртывании. Никакие токены в репозитории не хранятся. Секреты и runtime state исключены из git и Docker build context.

API-документация реализации: [Telegram Bot API](https://core.telegram.org/bots/api), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [OpenAI Images](https://developers.openai.com/api/reference/resources/images/methods/generate). Версии моделей и стоимость должны быть проверены установщиком по текущей документации и доступу проекта.
