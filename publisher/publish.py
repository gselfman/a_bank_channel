#!/usr/bin/env python3
"""Read queued JSON posts from public GitHub and publish due ones to Telegram."""
import datetime as dt
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request

REPO = os.getenv("GITHUB_REPO", "gselfman/a_bank_channel")
BRANCH = os.getenv("GITHUB_BRANCH", "main")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
DB = os.getenv("PUBLISHER_DB", "/var/lib/a-bank-publisher/sent.sqlite3")
UA = "A-Bank-Channel-Publisher/1.0"

def request_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def main():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, path TEXT NOT NULL, message_id TEXT NOT NULL, sent_at TEXT NOT NULL)")
    conn.commit()
    root = "https://api.github.com/repos/" + REPO + "/contents/posts/pending?ref=" + urllib.parse.quote(BRANCH)
    try:
        entries = request_json(root)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print("No posts/pending directory; nothing to publish.")
            return
        raise
    if not isinstance(entries, list):
        raise RuntimeError("Expected GitHub directory listing")
    now = dt.datetime.now(dt.timezone.utc)
    for entry in sorted(entries, key=lambda item: item.get("name", "")):
        path = entry.get("path", "")
        if not path.endswith(".json") or entry.get("type") != "file":
            continue
        # Pin file content to listing SHA, avoiding mutable branch race.
        blob = request_json("https://api.github.com/repos/" + REPO + "/git/blobs/" + entry["sha"])
        import base64
        post = json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
        if post.get("status") != "ready":
            continue
        post_id = str(post["id"])
        if conn.execute("SELECT 1 FROM sent WHERE id=?", (post_id,)).fetchone():
            continue
        publish_at = dt.datetime.fromisoformat(post["publish_at"].replace("Z", "+00:00"))
        if publish_at.tzinfo is None:
            raise ValueError("publish_at must include timezone: " + path)
        if publish_at > now:
            continue
        msg = str(post["text"]).strip()
        if not msg or len(msg) > 4096:
            raise ValueError("Telegram text length must be 1..4096: " + path)
        payload = urllib.parse.urlencode({"chat_id": CHAT, "text": msg, "disable_web_page_preview": "false"}).encode()
        result = request_json("https://api.telegram.org/bot" + TOKEN + "/sendMessage", payload)
        if not result.get("ok"):
            raise RuntimeError("Telegram refused post: " + path)
        conn.execute("INSERT INTO sent (id,path,message_id,sent_at) VALUES (?,?,?,?)",
                     (post_id, path, str(result["result"]["message_id"]), dt.datetime.now(dt.timezone.utc).isoformat()))
        conn.commit()
        print("Published:", post_id)

if __name__ == "__main__":
    main()
