import argparse
import datetime as dt
import html
import json
import logging
import os
import re
import time

from . import ai, sources
from .config import load
from .db import backup, connect, event, getmeta, lock, setmeta
from .telegram import Telegram, finalize, send_next, validate_caption

MOSCOW=dt.timezone(dt.timedelta(hours=3))


def seed(db,cfg):
    items=json.loads((cfg['root']/'content/posts.json').read_text())['posts']
    for p in items:
        image=str(cfg['root']/'content'/p['image']) if p['id'] != 28 else None
        validate_caption(p['text_html'], bool(image))
        db.execute('INSERT OR IGNORE INTO posts(id,title,caption,image,status,position,evidence,approved_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                   ('seed-'+str(p['id']),p['title'],p['text_html'],image,'approved',p['publish_order'],json.dumps(p.get('proof',[])),time.time(),time.time()))
    db.commit()
    setmeta(db,'seed_approval','User approved texts and covers in conversation on 2026-10-26')
    print('Imported 35 approved seed posts; existing state preserved.')


def approve(db,cfg,post_id):
    row=db.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
    if not row or row['status'] not in ('draft','hold'):
        raise RuntimeError('Only drafts or held posts can be approved')
    if not ai.fresh_evidence(db,json.loads(row['evidence'])):
        raise RuntimeError('Source evidence expired or changed; regenerate/review first')
    if not row['image']:
        raise RuntimeError('Generate and visually review the cover before approval')
    validate_caption(row['caption'])
    db.execute("UPDATE posts SET status='approved',approved_at=? WHERE id=?",(time.time(),post_id));db.commit()
    event(db,'approved',post_id)


def auto_schedule(db,cfg):
    if not cfg.get('schedule_enabled'):
        return
    now=dt.datetime.now(MOSCOW)
    for slot in cfg['publish_times_moscow']:
        hour,minute=map(int,slot.split(':'))
        due=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if due <= now:
            continue
        key='slot-'+due.isoformat()
        if getmeta(db,key):
            continue
        row=db.execute("SELECT id FROM posts WHERE status='approved' AND id NOT LIKE 'seed-%' ORDER BY approved_at LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE posts SET status='scheduled',due=? WHERE id=?",(due.timestamp(),row['id']));db.commit()
            setmeta(db,key,row['id'])


def tick(db,cfg):
    current=time.time()
    if current-float(getmeta(db,'last_poll_attempt','0')) >= cfg['poll_seconds']:
        setmeta(db,'last_poll_attempt',current)
        sites_ok=False
        try:
            sources.poll_sites(db,cfg);sites_ok=True
        except RuntimeError as exc:
            event(db,'scan_failed',str(exc))
        try:
            sources.poll_telegram(db,cfg)
        except RuntimeError as exc:
            event(db,'telegram_scan_failed',str(exc))
        if sites_ok and cfg.get('ai_enabled'):
            for row in db.execute("SELECT id FROM candidates WHERE status='new' ORDER BY created_at LIMIT ?",(cfg['drafts_per_poll'],)).fetchall():
                try:
                    ai.generate(db,cfg,row['id'])
                    if cfg.get('ai_images_enabled'):
                        generated=db.execute("SELECT id FROM posts WHERE id=? AND status='draft'",('ai-'+row['id'],)).fetchone()
                        if generated:ai.image(db,cfg,generated['id'])
                except Exception as exc:
                    event(db,'generation_failed',row['id']+': '+type(exc).__name__)
                    # Avoid paying for an invalid candidate again on every polling cycle.
                    db.execute("UPDATE candidates SET status='hold',reason=? WHERE id=?",('Generation failed; inspect events before retry',row['id']));db.commit()
                    logging.error('Draft generation failed for %s (%s)',row['id'],type(exc).__name__)
    auto_schedule(db,cfg)
    row=db.execute("SELECT * FROM posts WHERE status='scheduled' AND due<=? ORDER BY position LIMIT 1",(time.time(),)).fetchone()
    if row and not row['id'].startswith('seed-') and not ai.fresh_evidence(db,json.loads(row['evidence'])):
        db.execute("UPDATE posts SET status='hold',error='Source evidence expired or changed' WHERE id=?",(row['id'],));db.commit()
        event(db,'publication_held',row['id'])
    elif row and getmeta(db,'paused','1') != '1':
        tg=Telegram(cfg);tg.preflight()
        if send_next(db,cfg,tg):finalize(db,cfg,tg)
    setmeta(db,'heartbeat',time.time())


def review(db,cfg):
    rows=db.execute('SELECT * FROM posts ORDER BY position').fetchall()
    cards=[]
    for p in rows:
        image=''
        if p['image']:
            import base64
            from pathlib import Path
            path=Path(p['image'])
            if path.is_file():
                image='<img width="220" src="data:image/png;base64,'+base64.b64encode(path.read_bytes()).decode()+'">'
        cards.append('<article><h2>%s · %s</h2><p>%s</p>%s<pre>%s</pre><details><summary>Evidence</summary><pre>%s</pre></details></article>' %
                     (html.escape(p['id']),html.escape(p['title']),html.escape(p['status']),image,p['caption'],html.escape(p['evidence'])))
    dest=cfg['state']/'review.html'
    dest.write_text('<!doctype html><meta charset="utf-8"><title>A-Bank review</title><style>body{font:16px system-ui;max-width:950px;margin:40px auto}article{border-bottom:1px solid #bbb;padding:20px}pre{white-space:pre-wrap;font:inherit}</style>'+''.join(cards))
    print(dest)


def cli():
    parser=argparse.ArgumentParser(description='A-Bank channel worker')
    parser.add_argument('command',choices=['seed','status','preflight','review','poll','generate','image','approve','schedule','pause','resume','publish-initial','finalize','worker','once','reconcile','confirm-status','backup','reject'])
    parser.add_argument('id',nargs='?')
    parser.add_argument('--at',help='ISO datetime WITH UTC offset, e.g. 2026-10-27T10:00:00+03:00')
    parser.add_argument('--message-id',type=int)
    parser.add_argument('--not-sent',action='store_true',help='Operator verified in Telegram that delivery did NOT happen')
    parser.add_argument('--live',action='store_true',help='Actually send the already approved initial batch')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    os.umask(0o077)
    cfg=load()
    if args.command=='worker':
        while True:
            with lock(cfg):
                db=connect(cfg)
                try:tick(db,cfg)
                except Exception as exc:
                    event(db,'worker_error',type(exc).__name__)
                    logging.error('Worker cycle failed (%s); inspect state',type(exc).__name__)
                finally:db.close()
            time.sleep(30)
    with lock(cfg):
        db=connect(cfg)
        cmd=args.command
        if cmd=='seed':seed(db,cfg)
        elif cmd=='status':
            print(json.dumps({'paused':getmeta(db,'paused','1'),'heartbeat':getmeta(db,'heartbeat'),'posts':[dict(r) for r in db.execute('SELECT id,title,status,message_id,due,error FROM posts ORDER BY position')],'candidates':[dict(r) for r in db.execute('SELECT id,status,reason FROM candidates ORDER BY created_at DESC LIMIT 30')],'events':[dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 15')]},ensure_ascii=False,indent=2))
        elif cmd=='preflight':print(json.dumps(Telegram(cfg).preflight(),ensure_ascii=False))
        elif cmd=='review':review(db,cfg)
        elif cmd=='poll':sources.poll_sites(db,cfg);sources.poll_telegram(db,cfg)
        elif cmd=='generate':ai.generate(db,cfg,args.id)
        elif cmd=='image':ai.image(db,cfg,args.id)
        elif cmd=='approve':approve(db,cfg,args.id)
        elif cmd=='reject':
            db.execute("UPDATE posts SET status='rejected' WHERE id=? AND status IN ('draft','hold','approved','scheduled')",(args.id,));db.commit()
        elif cmd=='schedule':
            date=dt.datetime.fromisoformat(args.at or '')
            if date.tzinfo is None or date.timestamp()<=time.time():raise RuntimeError('Schedule needs a future time with explicit timezone')
            changed=db.execute("UPDATE posts SET status='scheduled',due=? WHERE id=? AND status='approved' AND id NOT LIKE 'seed-%'",(date.timestamp(),args.id)).rowcount;db.commit()
            if not changed:raise RuntimeError('Only approved non-seed posts can be scheduled')
        elif cmd in ('pause','resume'):setmeta(db,'paused','1' if cmd=='pause' else '0')
        elif cmd=='confirm-status':
            setmeta(db,'status_confirmed_at',time.time());event(db,'owner_status_checked','SBP notice checked against current conditions by deployment operator')
        elif cmd=='publish-initial':
            seed(db,cfg)
            if not args.live:
                print('Dry run only. Run preflight, verify current post 35, confirm-status, resume, then publish-initial --live.');return
            tg=Telegram(cfg);info=tg.preflight();print(json.dumps(info,ensure_ascii=False))
            if time.time()-float(getmeta(db,'status_confirmed_at','0'))>86400:raise RuntimeError('Check current funding status and run confirm-status first')
            if getmeta(db,'paused','1')=='1':raise RuntimeError('Publisher paused; use resume')
            while send_next(db,cfg,tg,initial=True):
                time.sleep(cfg['initial_spacing_seconds'])
            if db.execute("SELECT 1 FROM posts WHERE id LIKE 'seed-%' AND status!='sent'").fetchone():raise RuntimeError('Initial batch incomplete; inspect status before continuing')
            finalize(db,cfg,tg)
        elif cmd=='finalize':finalize(db,cfg)
        elif cmd=='reconcile':
            row=db.execute('SELECT * FROM posts WHERE id=?',(args.id,)).fetchone()
            if not row or row['status'] not in ('sending','uncertain','failed'):raise RuntimeError('Only uncertain/failed sends can be reconciled')
            if bool(args.message_id)==bool(args.not_sent):raise RuntimeError('Supply exactly one: --message-id or --not-sent after inspecting the channel')
            if args.message_id and args.message_id<=0:raise RuntimeError('Invalid message ID')
            status='sent' if args.message_id else ('approved' if args.id.startswith('seed-') else 'scheduled')
            db.execute('UPDATE posts SET status=?,message_id=?,error=NULL,sent_at=? WHERE id=?',(status,args.message_id,time.time() if args.message_id else None,args.id));db.commit()
            event(db,'manual_reconcile',args.id+': '+status)
        elif cmd=='backup':
            target=cfg['state']/('backup-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');backup(db,target);print(target)
        elif cmd in ('once','worker'):
            while True:
                try:tick(db,cfg)
                except Exception as exc:
                    # Error bodies/tracebacks may contain secrets; log only type and safe event code.
                    event(db,'worker_error',type(exc).__name__);logging.error('Worker cycle failed (%s); inspect state',type(exc).__name__)
                if cmd=='once':break
                time.sleep(30)


if __name__=='__main__':
    try:cli()
    except Exception as exc:
        print('Stopped:',str(exc) if isinstance(exc,(RuntimeError,ValueError)) else type(exc).__name__)
        raise SystemExit(1)
