# A-Bank publisher (Ubuntu 20.04)

The editorial ChatGPT task is intended to create posts in `posts/pending/*.json`.
The server checks the public GitHub repository and publishes due posts via the Telegram Bot API.
It does not use OpenAI API. Automatic GitHub writes by scheduled ChatGPT tasks are **not yet verified**.

## Post format
```json
{
  "id": "2026-10-27-001",
  "title": "Example",
  "text": "Telegram-ready message with source URL",
  "source": "https://example.com/news",
  "publish_at": "2026-10-27T12:00:00+03:00",
  "image": null,
  "status": "ready"
}
```

Only status=ready entries are published. Images are currently ignored.
SQLite stores Telegram message IDs and prevents reposting the same id on subsequent runs.
There is a small residual duplicate risk if Telegram accepts a message and the process crashes before the SQLite commit.
Avoid reusing an id for a different post.

## Install (run on Ubuntu as an administrator)
```bash
sudo adduser --system --group --no-create-home a-bank
sudo git clone https://github.com/gselfman/a_bank_channel.git /opt/a_bank_channel
sudo chown -R root:root /opt/a_bank_channel
sudo cp /opt/a_bank_channel/publisher/a-bank-publisher.service /etc/systemd/system/
sudo cp /opt/a_bank_channel/publisher/a-bank-publisher.timer /etc/systemd/system/
sudo install -m 600 /dev/null /etc/a-bank-publisher.env
sudo nano /etc/a-bank-publisher.env
```
Fill /etc/a-bank-publisher.env with:
```dotenv
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=@your_channel_username
GITHUB_REPO=gselfman/a_bank_channel
GITHUB_BRANCH=main
PUBLISHER_DB=/var/lib/a-bank-publisher/sent.sqlite3
```
Give the bot administrator permission to post to the target channel.
Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now a-bank-publisher.timer
sudo systemctl list-timers a-bank-publisher.timer
sudo journalctl -u a-bank-publisher.service -n 40 --no-pager
```
Code updates:
```bash
cd /opt/a_bank_channel && sudo git pull --ff-only
```
**Security:** Never commit the Telegram bot token. Public GitHub exposes unpublished drafts.
A private repository would require authenticated GitHub API requests. Consider a separate private queue.
**Operational:** Setup is not performed on the user's server by this repository commit; install must be executed separately.
