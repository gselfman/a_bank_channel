import html
import re
import time
from pathlib import Path

from .db import event, getmeta, setmeta
from .net import RemoteError, multipart, request


def plain(text):
    return html.unescape(re.sub('<[^>]+>', '', text))


def validate_caption(text, photo=True):
    if len(plain(text)) > (1024 if photo else 4096):
        raise ValueError('Telegram caption/message is too long')


class Telegram:
    def __init__(self, cfg):
        self.cfg = cfg
        if not cfg.get('TELEGRAM_BOT_TOKEN'):
            raise RuntimeError('TELEGRAM_BOT_TOKEN is not configured')
        self.base = 'https://api.telegram.org/bot' + cfg['TELEGRAM_BOT_TOKEN'] + '/'

    def call(self, method, fields, image=None):
        payload, headers = (multipart(fields, 'photo', image) if image else (fields, {}))
        result = request(self.base + method, payload, headers, timeout=90)
        if not result.get('ok'):
            raise RemoteError(result.get('error_code', 'telegram'), result.get('parameters', {}).get('retry_after', 0))
        return result['result']

    def preflight(self):
        me = self.call('getMe', {})
        chat = self.call('getChat', {'chat_id': self.cfg['TELEGRAM_CHANNEL']})
        if chat['type'] != 'channel':
            raise RuntimeError('Destination must be a Telegram channel')
        member = self.call('getChatMember', {'chat_id': chat['id'], 'user_id': me['id']})
        if member['status'] != 'creator' and not (member.get('can_post_messages') and member.get('can_edit_messages')):
            raise RuntimeError('Bot needs channel permissions to post and edit/pin messages')
        return {'bot': me['username'], 'channel': chat.get('username', str(chat['id'])), 'chat_id': str(chat['id'])}


def navigation(db, channel):
    username = channel.lstrip('@')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,}', username):
        raise RuntimeError('Navigation currently requires a public @channel username')
    lines = ['🧭 <b>Навигация по каналу АБанка</b>', '']
    for row in db.execute("SELECT * FROM posts WHERE status='sent' AND id NOT IN ('seed-28','seed-35') ORDER BY position"):
        lines.append('<a href="https://t.me/%s/%s">%s</a>' % (username, row['message_id'], html.escape(row['title'])))
    status = db.execute("SELECT message_id FROM posts WHERE id='seed-35' AND status='sent'").fetchone()
    if status:
        lines.extend(['', '<a href="https://t.me/%s/%s">Актуальный статус пополнения</a>' % (username, status['message_id'])])
    lines.extend(['', 'Поддержка: @a_bank_support_bot'])
    text = '\n'.join(lines)
    validate_caption(text, False)
    return text


def send_next(db, cfg, tg=None, initial=False):
    if getmeta(db, 'paused', '1') == '1':
        return False
    # A lost response cannot be safely retried: Telegram has no send idempotency key.
    if db.execute("SELECT 1 FROM posts WHERE status IN ('sending','uncertain','failed')").fetchone():
        raise RuntimeError('Uncertain delivery: reconcile the message before resuming')
    where = "status='approved' AND id LIKE 'seed-%'" if initial else "status='scheduled' AND due<=?"
    args = () if initial else (time.time(),)
    row = db.execute('SELECT * FROM posts WHERE ' + where + ' ORDER BY position LIMIT 1', args).fetchone()
    if not row:
        return False
    if initial and row['due'] and row['due'] > time.time():
        raise RuntimeError('Telegram retry_after has not elapsed; retry the initial batch later')
    if row['id'] == 'seed-35':
        confirmed = float(getmeta(db, 'status_confirmed_at', '0'))
        if time.time() - confirmed > 86400:
            raise RuntimeError('Verify current SBP status; run confirm-status after checking it')
    channel = cfg['TELEGRAM_CHANNEL']
    bound = getmeta(db, 'channel')
    if bound and bound != channel:
        raise RuntimeError('Database is bound to another channel')
    tg = tg or Telegram(cfg)
    text = navigation(db, channel) if row['id'] == 'seed-28' else row['caption']
    image = Path(row['image']) if row['image'] else None
    validate_caption(text, bool(image))
    if image and (not image.is_file() or image.stat().st_size > 10 * 1024 * 1024):
        raise RuntimeError('Missing or oversized cover')
    setmeta(db, 'channel', channel)
    db.execute("UPDATE posts SET status='sending',channel=? WHERE id=?", (channel, row['id']))
    db.commit()
    try:
        fields = {'chat_id': channel, 'parse_mode': 'HTML', 'disable_notification': 'true'}
        if image:
            fields['caption'] = text
            result = tg.call('sendPhoto', fields, image)
        else:
            fields.update(text=text, link_preview_options={'is_disabled': True})
            result = tg.call('sendMessage', fields)
    except RemoteError as exc:
        status = 'uncertain' if exc.uncertain else ('approved' if initial else 'scheduled') if exc.code == 429 else 'failed'
        db.execute('UPDATE posts SET status=?,error=?,due=? WHERE id=?', (status, str(exc), time.time() + max(60, exc.retry_after), row['id']))
        db.commit()
        if status != 'scheduled':
            setmeta(db, 'paused', '1')
        event(db, 'telegram_error', row['id'] + ': ' + status)
        raise
    # If the process dies before this commit, status=sending blocks ALL automatic resends.
    db.execute("UPDATE posts SET status='sent',message_id=?,sent_at=?,caption=?,error=NULL WHERE id=?", (result['message_id'], time.time(), text, row['id']))
    db.commit()
    event(db, 'published', row['id'])
    return True


def finalize(db, cfg, tg=None):
    if getmeta(db, 'channel') not in (None, cfg['TELEGRAM_CHANNEL']):
        raise RuntimeError('Database is bound to another channel')
    tg = tg or Telegram(cfg)
    for post_id in ('seed-28', 'seed-35'):
        row = db.execute("SELECT * FROM posts WHERE id=? AND status='sent'", (post_id,)).fetchone()
        if not row:
            continue
        if post_id == 'seed-28':
            text = navigation(db, cfg['TELEGRAM_CHANNEL'])
            if text != row['caption']:
                try:
                    tg.call('editMessageText', {'chat_id': cfg['TELEGRAM_CHANNEL'], 'message_id': row['message_id'], 'text': text, 'parse_mode': 'HTML', 'link_preview_options': {'is_disabled': True}})
                except RemoteError as exc:
                    if not exc.not_modified:raise
                db.execute('UPDATE posts SET caption=? WHERE id=?', (text, post_id)); db.commit()
        if getmeta(db, 'pinned-' + post_id) != str(row['message_id']):
            tg.call('pinChatMessage', {'chat_id': cfg['TELEGRAM_CHANNEL'], 'message_id': row['message_id'], 'disable_notification': True})
            setmeta(db, 'pinned-' + post_id, row['message_id'])
