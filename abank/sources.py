"""Read-only, unauthenticated public web monitoring; no Telegram account session."""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from .db import event, getmeta, setmeta

ALLOWED = {'altyn.one', 'www.altyn.one', 'a-bank.ru', 'www.a-bank.ru', 't.me'}


class Node:
    def __init__(self, tag='', attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        return ''.join(x.text() if isinstance(x, Node) else x for x in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def has(self, css):
        return css in self.attrs.get('class', '').split()


class DOM(HTMLParser):
    def __init__(self, data):
        super().__init__(convert_charrefs=True)
        self.root = Node(); self.stack = [self.root]
        self.feed(data)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(node)
        if tag in ('br', 'p', 'div', 'li', 'h1', 'h2', 'h3'):
            node.children.append('\n')

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]; break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalize(text):
    return '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())


def allowed(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == 'https' and parts.hostname in ALLOWED and not parts.username and not parts.password and parts.port in (None,443)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise RuntimeError('Source redirected outside the allowed sites')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    if not allowed(url):
        raise RuntimeError('Source URL is outside the allowlist')
    req = urllib.request.Request(url, headers={'User-Agent': 'ABankEditorialMonitor/1.0 (public content research)'})
    try:
        with urllib.request.build_opener(SafeRedirect()).open(req, timeout=30) as res:
            raw = res.read(3 * 1024 * 1024 + 1)
            if len(raw) > 3 * 1024 * 1024:
                raise RuntimeError('Source page exceeds size limit')
            content = raw.decode('utf-8', errors='replace')
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError('Source is unavailable: ' + url) from None
    if any(marker in content.lower() for marker in ('cf-chl-', 'just a moment...', 'verify you are human')):
        raise RuntimeError('Source returned an access challenge: ' + url)
    return content


def telegram_messages(content, channel):
    dom = DOM(content)
    posts = []
    for node in dom.root.walk():
        identity = node.attrs.get('data-post', '')
        if not node.has('tgme_widget_message') or not identity.startswith(channel + '/'):
            continue
        nodes = list(node.walk())
        text = next((normalize(n.text()) for n in nodes if n.has('tgme_widget_message_text')), '')
        video = any(n.has('tgme_widget_message_video_player') or n.tag == 'video' for n in nodes)
        date = next((n.attrs.get('datetime') for n in nodes if n.tag == 'time'), None)
        posts.append({'number': int(identity.split('/')[-1]), 'text': text, 'video': video, 'date': date, 'url': 'https://t.me/' + identity})
    if not posts:
        raise RuntimeError('Public Telegram page contains no messages; access or layout changed')
    return sorted(posts, key=lambda p:p['number'])


def candidate(db, identity, url, title, body, source_time=None, excluded=False):
    db.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?,?,?)',
               (identity, url, title[:180], body[:24000], source_time, 'excluded' if excluded else 'new', 'video or empty message' if excluded else None, time.time()))
    db.commit()


def poll_telegram(db, cfg):
    channel = cfg['source_channel']
    cursor = getmeta(db, 'telegram_cursor')
    seen = []
    url = 'https://t.me/s/' + channel
    for page in range(cfg.get('telegram_max_pages', 20)):
        batch = telegram_messages(fetch(url), channel)
        seen.extend(batch)
        if cursor is None or min(p['number'] for p in batch) <= int(cursor):
            break
        url = 'https://t.me/s/%s?before=%s' % (channel, min(p['number'] for p in batch))
    else:
        raise RuntimeError('Telegram history gap exceeds page limit; cursor NOT advanced')
    if cursor is None:
        # First run establishes a baseline; historical content already supplied in seeds.
        setmeta(db, 'telegram_cursor', max(p['number'] for p in seen))
        event(db, 'telegram_baseline', len(seen))
        return
    for post in sorted({p['number']:p for p in seen}.values(), key=lambda p:p['number']):
        if post['number'] > int(cursor):
            candidate(db, 'tg-' + str(post['number']), post['url'], post['text'].split('\n')[0] if post['text'] else 'Без текста', post['text'], post['date'], post['video'] or not post['text'])
    setmeta(db, 'telegram_cursor', max(int(cursor), max(p['number'] for p in seen)))


def poll_sites(db, cfg):
    urls = list(cfg['source_pages'])
    visited = set()
    errors = []
    while urls and len(visited) < cfg.get('site_max_pages', 25):
        url = urls.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            dom = DOM(fetch(url))
            body = normalize(dom.root.text())
            if len(body) < 100:
                raise RuntimeError('Source returned too little readable content: ' + url)
            title = next((normalize(n.text()) for n in dom.root.walk() if n.tag == 'title'), url)
            kind = 'abank' if urllib.parse.urlsplit(url).hostname.endswith('a-bank.ru') else 'altyn'
            previous = db.execute('SELECT digest FROM documents WHERE url=?', (url,)).fetchone()
            sha = digest(body)
            db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET digest=excluded.digest,title=excluded.title,body=excluded.body,fetched_at=excluded.fetched_at', (url, kind, sha, title, body[:100000], time.time()))
            db.commit()
            if previous and previous['digest'] != sha:
                candidate(db, 'web-' + digest(url + sha), url, title, body)
            host = urllib.parse.urlsplit(url).hostname
            for n in dom.root.walk():
                link = urllib.parse.urljoin(url, n.attrs.get('href', ''))
                parts = urllib.parse.urlsplit(link)
                canonical = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, '', ''))
                if n.tag == 'a' and allowed(canonical) and parts.hostname == host and not parts.query and not any(parts.path.lower().endswith(ext) for ext in ('.pdf','.png','.jpg','.zip','.svg','.mp4','.jpeg','.webp')):
                    if canonical not in visited and canonical not in urls:
                        urls.append(canonical)
        except RuntimeError as exc:
            errors.append(str(exc)); event(db, 'source_error', str(exc))
    if errors:
        raise RuntimeError('Some source pages failed; see events. Draft generation skipped for this cycle.')
    setmeta(db, 'last_site_scan', time.time())
