import contextlib
import fcntl
import json
import sqlite3
import time


def connect(cfg):
    db = sqlite3.connect(str(cfg['state'] / 'channel.sqlite3'), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS posts (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, caption TEXT NOT NULL,
      image TEXT, status TEXT NOT NULL, position INTEGER NOT NULL,
      due REAL, message_id INTEGER, channel TEXT, evidence TEXT NOT NULL DEFAULT '[]',
      approved_at REAL, created_at REAL NOT NULL, error TEXT, sent_at REAL);
    CREATE TABLE IF NOT EXISTS documents (
      url TEXT PRIMARY KEY, kind TEXT NOT NULL, digest TEXT NOT NULL,
      title TEXT NOT NULL, body TEXT NOT NULL, fetched_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS candidates (
      id TEXT PRIMARY KEY, source_url TEXT NOT NULL, title TEXT NOT NULL,
      body TEXT NOT NULL, source_time TEXT, status TEXT NOT NULL,
      reason TEXT, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS api_calls (
      id INTEGER PRIMARY KEY, month TEXT NOT NULL, kind TEXT NOT NULL,
      reserved_usd REAL NOT NULL, created_at REAL NOT NULL, usage TEXT);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY, created_at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
    ''')
    return db


def getmeta(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default


def setmeta(db, key, value):
    db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    db.commit()


def event(db, kind, detail):
    db.execute('INSERT INTO events(created_at,kind,detail) VALUES (?,?,?)', (time.time(), kind, str(detail)))
    db.commit()


@contextlib.contextmanager
def lock(cfg):
    with (cfg['state'] / 'worker.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another A-Bank process is running') from None
        yield


def backup(db, destination):
    with sqlite3.connect(str(destination)) as target:
        db.backup(target)
