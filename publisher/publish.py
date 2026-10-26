#!/usr/bin/env python3
"""Publish due, image-backed GitHub queue posts to Telegram; no OpenAI API."""
import base64
import datetime as dt
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = os.getenv("GITHUB_REPO", "gselfman/a_bank_channel")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
DB = os.getenv("PUBLISHER_DB", "/var/lib/a-bank-publisher/sent.sqlite3")
UA = "A-Bank-GitHub-Publisher/2.0"

def fetch(url, fields=None):
    data = urllib.parse.urlencode(fields).encode("utf-8") if fields is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.load(resp)

def telegram(method, **fields):
    response = fetch("https://api.telegram.org/bot" + TOKEN + "/" + method, fields)
    if not response.get("ok"):
        raise RuntimeError("Telegram returned ok=false for " + method)
    return str(response["result"]["message_id"])

def image_url(image):
    if not isinstance(image, str) or not image.strip():
        raise ValueError("image is required: use repository-relative path or HTTPS URL")
    image = image.strip()
    if image.startswith("https://"):
        return image
    if image.startswith("/") or ".." in image.split("/") or image.startswith("http:"):
        raise ValueError("unsafe image path")
    return "https://raw.githubusercontent.com/" + REPO + "/" + urllib.parse.quote(BRANCH, safe="") + "/" + urllib.parse.quote(image, safe="/")

def main():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, path TEXT NOT NULL, message_id TEXT NOT NULL, sent_at TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS photo_sent (id TEXT PRIMARY KEY, message_id TEXT NOT NULL)")
    db.commit()
    base = "https://api.github.com/repos/" + REPO
    queue = base + "/contents/posts/pending?ref=" + urllib.parse.quote(BRANCH)
    try:
        entries = fetch(queue)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("Queue is empty: posts/pending does not exist")
            return
        raise
    if not isinstance(entries, list):
        raise RuntimeError("Expected GitHub directory listing")
    now = dt.datetime.now(dt.timezone.utc)
    for item in sorted(entries, key=lambda x: x.get("name", "")):
        path = item.get("path", "")
        if item.get("type") != "file" or not path.endswith(".json"):
            continue
        blob = fetch(base + "/git/blobs/" + item["sha"])
        post = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
        if post.get("status") != "ready":
            continue
        post_id = str(post["id"])
        if db.execute("SELECT 1 FROM sent WHERE id=?", (post_id,)).fetchone():
            continue
        when = dt.datetime.fromisoformat(post["publish_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            raise ValueError("publish_at requires timezone: " + path)
        if when > now:
            continue
        message = str(post["text"]).strip()
        if not 1 <= len(message) <= 4096:
            raise ValueError("Text must be 1..4096 characters: " + path)
        photo = image_url(post.get("image"))
        prev = db.execute("SELECT message_id FROM photo_sent WHERE id=?", (post_id,)).fetchone()
        if len(message) <= 1024 and not prev:
            mid = telegram("sendPhoto", chat_id=CHAT, photo=photo, caption=message)
        else:
            if not prev:
                photo_mid = telegram("sendPhoto", chat_id=CHAT, photo=photo)
                db.execute("INSERT INTO photo_sent(id,message_id) VALUES (?,?)", (post_id, photo_mid))
                db.commit()
            mid = telegram("sendMessage", chat_id=CHAT, text=message, disable_web_page_preview="true")
        db.execute("INSERT INTO sent(id,path,message_id,sent_at) VALUES (?,?,?,?)",
                   (post_id, path, mid, dt.datetime.now(dt.timezone.utc).isoformat()))
        db.commit()
        print("Published:", post_id, mid)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERROR:", type(error).__name__, str(error), file=sys.stderr)
        sys.exit(1)
