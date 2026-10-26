"""Regenerate a small static preview using the exact production captions."""
import html
import json
from pathlib import Path

root=Path(__file__).resolve().parent.parent
data=json.loads((root/'content/posts.json').read_text())
cards=[]
for p in data['posts']:
    cards.append('<article id="p%s"><p>Порядок %s · Тема %s · Согласовано, не опубликовано</p><h2>%s</h2><img src="%s" alt="Обложка"><div class="caption">%s</div><details><summary>Редакторская заметка (не публикуется)</summary>%s</details></article>' % (p['id'],p['publish_order'],p['id'],html.escape(p['title']),p['image'],p['text_html'],html.escape(p['editor_note'])))
(root/'content/review.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>АБанк — 35 согласованных публикаций</title><style>body{font:16px/1.6 system-ui;background:#161616;color:#f6ecd8;max-width:980px;padding:20px;margin:auto}article{background:#242424;border:1px solid #6e582c;border-radius:16px;padding:24px;margin:24px 0;overflow:auto}img{width:240px;max-width:100%;float:right;margin:0 0 16px 24px;border-radius:12px}.caption{white-space:pre-wrap}a{color:#edd39e}details{clear:both;font-size:13px;padding-top:20px}h2{font-size:22px}@media(max-width:600px){img{float:none;margin:0 0 20px}}</style><h1>35 публикаций АБанка</h1><p>Тексты и оформление согласованы владельцем 26.10.2026. Фактическое состояние отправки хранится только в SQLite на сервере. Навигация (тема 28) отправляется текстом и получает реальные ссылки; временный статус (тема 35) требует проверки перед публикацией.</p>'+''.join(cards)+'</html>')
