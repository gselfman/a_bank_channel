import datetime
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from abank import ai, sources
from abank.config import ROOT
from abank.db import connect, getmeta, lock, setmeta
from abank.main import auto_schedule, seed
from abank.net import RemoteError
from abank.telegram import finalize, navigation, send_next, validate_caption


class FakeTelegram:
    def __init__(self, error=None):self.calls=[];self.error=error
    def call(self, method, fields, image=None):
        self.calls.append((method,fields,image))
        if self.error:raise self.error
        return {'message_id':100+len(self.calls)}


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=json.loads((ROOT/'config.json').read_text())
        self.cfg.update(root=ROOT,state=Path(self.tmp.name),TELEGRAM_CHANNEL='@a_bank_wallet')
        self.db=connect(self.cfg)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def ready(self):seed(self.db,self.cfg);setmeta(self.db,'paused','0');setmeta(self.db,'status_confirmed_at',time.time())
    def test_seeds_idempotent_and_links(self):
        self.ready();seed(self.db,self.cfg)
        rows=self.db.execute('SELECT * FROM posts ORDER BY position').fetchall()
        self.assertEqual(len(rows),35)
        self.assertEqual([r['id'] for r in rows][-2:],['seed-28','seed-35'])
        for r in rows:
            self.assertIn('start=SAhtmEI">@abank_wallet_bot</a>',r['caption'])
            self.assertIn('a_bank=SAhtmEI">https://wallet.a-bank.ru</a>',r['caption'])
            validate_caption(r['caption'],r['id']!='seed-28')
    def test_publish_order_restart_no_duplicates(self):
        self.ready();tg=FakeTelegram()
        for _ in range(35):self.assertTrue(send_next(self.db,self.cfg,tg,initial=True))
        self.assertFalse(send_next(self.db,self.cfg,tg,initial=True))
        self.assertEqual(len(tg.calls),35)
        self.assertEqual(tg.calls[-2][0],'sendMessage')
        self.assertIn('t.me/a_bank_wallet/',tg.calls[-2][1]['text'])
        self.assertIn('СБП',tg.calls[-1][1]['caption'])
        self.assertIn('Актуальный статус',navigation(self.db,'@a_bank_wallet'))
    def test_uncertain_send_stops_retry(self):
        self.ready();tg=FakeTelegram(RemoteError('network',uncertain=True))
        with self.assertRaises(RemoteError):send_next(self.db,self.cfg,tg,initial=True)
        self.assertEqual(self.db.execute("SELECT status FROM posts WHERE id='seed-1'").fetchone()[0],'uncertain')
        setmeta(self.db,'paused','0')
        with self.assertRaises(RuntimeError):send_next(self.db,self.cfg,FakeTelegram(),initial=True)
    def test_crash_state_blocks_resend(self):
        self.ready();self.db.execute("UPDATE posts SET status='sending' WHERE id='seed-1'");self.db.commit()
        with self.assertRaises(RuntimeError):send_next(self.db,self.cfg,FakeTelegram(),initial=True)
    def test_failed_send_blocks_skipping_post(self):
        self.ready();tg=FakeTelegram(RemoteError(400))
        with self.assertRaises(RemoteError):send_next(self.db,self.cfg,tg,initial=True)
        setmeta(self.db,'paused','0')
        with self.assertRaises(RuntimeError):send_next(self.db,self.cfg,FakeTelegram(),initial=True)
    def test_rate_limit_preserves_position(self):
        self.ready();tg=FakeTelegram(RemoteError(429,120))
        with self.assertRaises(RemoteError):send_next(self.db,self.cfg,tg,initial=True)
        row=self.db.execute("SELECT * FROM posts WHERE id='seed-1'").fetchone()
        self.assertEqual(row['status'],'approved');self.assertGreater(row['due'],time.time()+100)
    def test_expired_status_blocks_last_post(self):
        self.ready();self.db.execute("UPDATE posts SET status='sent' WHERE id!='seed-35'");self.db.commit()
        setmeta(self.db,'status_confirmed_at',0)
        with self.assertRaises(RuntimeError):send_next(self.db,self.cfg,FakeTelegram(),initial=True)
    def test_paused_never_sends(self):
        seed(self.db,self.cfg);tg=FakeTelegram()
        self.assertFalse(send_next(self.db,self.cfg,tg,initial=True));self.assertEqual(tg.calls,[])
    def test_budget_atomic_cap_and_no_free_retry(self):
        self.cfg['ai_budget'].update(monthly_usd=1,text_reservation_usd=.6,text_daily_calls=10)
        ai.reserve(self.db,self.cfg,'text')
        with self.assertRaises(RuntimeError):ai.reserve(self.db,self.cfg,'text')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM api_calls').fetchone()[0],1)
    def test_budget_disabled(self):
        with self.assertRaises(RuntimeError):ai.reserve(self.db,self.cfg,'text')
    def test_evidence_cannot_invent_quote(self):
        docs={'https://altyn.one/':{'body':'Поддерживается карта Visa для покупок.','digest':'h','fetched_at':time.time(),'kind':'altyn'}}
        out={'decision':'draft','evidence':[{'claim':'Visa','url':'https://altyn.one/','quote':'Поддерживается карта Visa'}]}
        self.assertEqual(len(ai.verify_evidence(out,docs)),1)
        out['evidence'][0]['quote']='Комиссия всегда ноль'
        with self.assertRaises(RuntimeError):ai.verify_evidence(out,docs)
    def test_stale_or_changed_evidence(self):
        self.db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',('https://altyn.one/','altyn','current','a','body',time.time()));self.db.commit()
        self.assertFalse(ai.fresh_evidence(self.db,[{'url':'https://altyn.one/','digest':'old'}]))
        self.assertTrue(ai.fresh_evidence(self.db,[{'url':'https://altyn.one/','digest':'current'}]))
    def test_telegram_parser_text_video_and_access_wall(self):
        fixture='''<div class="tgme_widget_message" data-post="altynwallet/100"><div class="tgme_widget_message_text">Привет<br>Новая функция</div><time datetime="2026-10-26T10:00:00+00:00"></time></div><div class="tgme_widget_message" data-post="altynwallet/101"><video></video></div>'''
        rows=sources.telegram_messages(fixture,'altynwallet')
        self.assertEqual(rows[0]['text'],'Привет\nНовая функция');self.assertTrue(rows[1]['video'])
        with self.assertRaises(RuntimeError):sources.telegram_messages('<h1>Login</h1>','altynwallet')
    def test_telegram_baseline_then_new_only(self):
        def page(n):return '<div class="tgme_widget_message" data-post="altynwallet/%s"><div class="tgme_widget_message_text">Новый пост</div></div>'%n
        with patch('abank.sources.fetch',return_value=page(100)):sources.poll_telegram(self.db,self.cfg)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],0)
        with patch('abank.sources.fetch',side_effect=[page(101),page(100)]):sources.poll_telegram(self.db,self.cfg)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM candidates').fetchone()[0],1)
        self.assertEqual(getmeta(self.db,'telegram_cursor'),'101')
    def test_source_allowlist(self):
        self.assertFalse(sources.allowed('https://altyn.one.evil.com/'))
        self.assertFalse(sources.allowed('http://127.0.0.1'))
        self.assertFalse(sources.allowed('https://user:pass@altyn.one/'))
        self.assertTrue(sources.allowed('https://t.me/s/altynwallet'))
    def test_caption_limit_after_parsing(self):
        validate_caption('<a href="https://example.com/long">'+'x'*1024+'</a>')
        with self.assertRaises(ValueError):validate_caption('x'*1025)
    def test_process_lock(self):
        with lock(self.cfg):
            with self.assertRaises(RuntimeError):
                with lock(self.cfg):pass
    def test_finalize_after_lost_edit_response(self):
        self.ready();tg=FakeTelegram()
        for _ in range(35):send_next(self.db,self.cfg,tg,initial=True)
        class AlreadyEdited(FakeTelegram):
            def call(self,method,fields,image=None):
                if method=='editMessageText':raise RemoteError(400,not_modified=True)
                return super().call(method,fields,image)
        finalize(self.db,self.cfg,AlreadyEdited())
        self.assertIn('Актуальный статус',self.db.execute("SELECT caption FROM posts WHERE id='seed-28'").fetchone()[0])
        self.assertIsNotNone(getmeta(self.db,'pinned-seed-35'))
    def test_finalize_refuses_wrong_channel(self):
        self.ready();setmeta(self.db,'channel','@different_channel')
        with self.assertRaises(RuntimeError):finalize(self.db,self.cfg,FakeTelegram())


if __name__=='__main__':unittest.main()
