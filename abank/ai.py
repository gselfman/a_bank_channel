import base64
import datetime
import html
import json
import re
import time

from .db import event
from .net import request
from .sources import digest
from .telegram import validate_caption


def reserve(db, cfg, kind):
    budget = cfg['ai_budget']
    amount = float(budget.get(kind + '_reservation_usd', 0))
    cap = float(budget.get('monthly_usd', 0))
    month = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m')
    day_start = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    db.execute('BEGIN IMMEDIATE')
    try:
        spent = db.execute('SELECT COALESCE(SUM(reserved_usd),0) FROM api_calls WHERE month=?', (month,)).fetchone()[0]
        calls = db.execute('SELECT COUNT(*) FROM api_calls WHERE kind=? AND created_at>=?', (kind, day_start)).fetchone()[0]
        if amount <= 0 or cap <= 0 or spent + amount > cap or calls >= budget.get(kind + '_daily_calls', 0):
            raise RuntimeError('AI budget or daily call allowance exhausted/not configured')
        cur = db.execute('INSERT INTO api_calls(month,kind,reserved_usd,created_at) VALUES (?,?,?,?)', (month, kind, amount, time.time()))
        db.commit()
        return cur.lastrowid
    except Exception:
        db.rollback(); raise


def api(db, cfg, kind, endpoint, payload):
    if not cfg.get('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY is not configured')
    call_id = reserve(db, cfg, kind)
    # Failed/unknown requests remain charged against the reservation. No paid auto-retries.
    result = request('https://api.openai.com/v1/' + endpoint, payload, {'Authorization': 'Bearer ' + cfg['OPENAI_API_KEY']}, timeout=240)
    db.execute('UPDATE api_calls SET usage=? WHERE id=?', (json.dumps(result.get('usage', {})), call_id)); db.commit()
    return result


def extract_text(result):
    if result.get('status') != 'completed':
        raise RuntimeError('AI response was incomplete or refused')
    return ''.join(c.get('text', '') for item in result.get('output', []) for c in item.get('content', []) if c.get('type') == 'output_text')


SCHEMA = {'type':'object','additionalProperties':False,'required':['decision','title','body','image_prompt','reason','evidence'], 'properties':{
    'decision':{'type':'string','enum':['draft','exclude','hold']},
    'title':{'type':'string'},'body':{'type':'string'},'image_prompt':{'type':'string'},'reason':{'type':'string'},
    'evidence':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['claim','url','quote'],'properties':{'claim':{'type':'string'},'url':{'type':'string'},'quote':{'type':'string'}}}}}}


def footer():
    return '\n\nTelegram-бот: <a href="https://telegram.me/Abank_wallet_bot?start=SAhtmEI">@abank_wallet_bot</a>\nВеб-версия: <a href="https://wallet.a-bank.ru/?a_bank=SAhtmEI">https://wallet.a-bank.ru</a>\nПоддержка: @a_bank_support_bot'


def verify_evidence(output, docs):
    verified = []
    for e in output['evidence']:
        doc = docs.get(e['url'])
        if not doc or len(e['quote'].strip()) < 12 or e['quote'] not in doc['body']:
            raise RuntimeError('AI evidence quote not found verbatim in current site snapshot')
        verified.append(dict(e, digest=doc['digest'], fetched_at=doc['fetched_at'], kind=doc['kind']))
    if output['decision'] == 'draft' and (not verified or not any(e['kind'] == 'altyn' for e in verified)):
        raise RuntimeError('Draft lacks current Altyn website evidence')
    return verified


def generate(db, cfg, candidate_id):
    row = db.execute('SELECT * FROM candidates WHERE id=?', (candidate_id,)).fetchone()
    if not row or row['status'] not in ('new','hold'):
        raise RuntimeError('Candidate must be new or held')
    docs = {d['url']:dict(d) for d in db.execute('SELECT * FROM documents WHERE fetched_at>?', (time.time()-86400,))}
    if not any(d['kind']=='altyn' for d in docs.values()) or not any(d['kind']=='abank' for d in docs.values()):
        raise RuntimeError('Fresh snapshots from BOTH websites are required')
    # Bound context size; sources, not the model, define the evidence universe.
    context = [{'url':d['url'],'kind':d['kind'],'body':d['body'][:12000]} for d in sorted(docs.values(), key=lambda d:(d['url'] not in cfg['source_pages'], d['url']))][:12]
    supplied = {d['url']:dict(docs[d['url']], body=d['body']) for d in context}
    payload = {'model':cfg['OPENAI_TEXT_MODEL'], 'store':False, 'max_output_tokens':2500,
               'instructions':(cfg['root']/'prompts/editor.txt').read_text(),
               'input':json.dumps({'candidate':dict(row),'existing_topics':[r['title'] for r in db.execute('SELECT title FROM posts ORDER BY position')], 'today_moscow':datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat(),'website_snapshots':context}, ensure_ascii=False),
               'text':{'format':{'type':'json_schema','name':'abank_editorial','strict':True,'schema':SCHEMA}}}
    output = json.loads(extract_text(api(db,cfg,'text','responses',payload)))
    evidence = verify_evidence(output, supplied)
    caption = html.escape(output['body']) + footer()
    if re.search(r'https?://|@[A-Za-z0-9_]+', output['body']):
        raise RuntimeError('AI body must not contain links or handles; trusted footer is added by code')
    if re.search(r'алтын|altyn|дзен|франшиз', output['body'], re.I):
        raise RuntimeError('Client text contains excluded source branding or franchise content')
    validate_caption(caption)
    status = output['decision']
    if status == 'draft':
        identity = 'ai-' + candidate_id
        fingerprint = digest(output['body'])
        if db.execute('SELECT 1 FROM posts WHERE caption=?', (caption,)).fetchone():
            status='exclude'; output['reason']='Exact duplicate of existing draft'
        else:
            db.execute('INSERT OR IGNORE INTO posts(id,title,caption,status,position,evidence,created_at) VALUES (?,?,?,?,?,?,?)',
                       (identity, output['title'], caption, 'draft', 1000+int(time.time()), json.dumps(evidence, ensure_ascii=False), time.time()))
            folder=cfg['state']/'drafts';folder.mkdir(exist_ok=True)
            (folder/(identity+'.json')).write_text(json.dumps(dict(output, body_hash=fingerprint),ensure_ascii=False,indent=2))
    db.execute('UPDATE candidates SET status=?,reason=? WHERE id=?', (status,output['reason'],candidate_id));db.commit()
    event(db,'ai_candidate',candidate_id+': '+status)


def image(db, cfg, post_id):
    row=db.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
    if not row or row['status'] not in ('draft','hold'):
        raise RuntimeError('Generate images before approval only')
    detail=json.loads((cfg['state']/'drafts'/(post_id+'.json')).read_text())
    prompt=(cfg['root']/'prompts/image.txt').read_text()+'\nSubject: '+detail['image_prompt']
    result=api(db,cfg,'image','images/generations',{'model':cfg['OPENAI_IMAGE_MODEL'],'prompt':prompt,'size':'1024x1024','quality':cfg.get('image_quality','medium'),'n':1})
    encoded=result['data'][0].get('b64_json')
    if not encoded:
        raise RuntimeError('Image response must include base64 bytes; external URLs are not fetched')
    from PIL import Image,ImageOps,ImageDraw
    import io
    cover=Image.open(io.BytesIO(base64.b64decode(encoded,validate=True))).convert('RGB')
    cover=ImageOps.fit(cover,(1024,1024))
    # Overlay the supplied logo exactly; do not ask AI to redraw the brand.
    logo=Image.open(cfg['root']/'brand/logo.png').convert('RGBA')
    logo.thumbnail((190,150))
    ImageDraw.Draw(cover).rounded_rectangle((28,28,244,206),radius=22,fill=(15,15,15))
    cover.paste(logo,(42+(190-logo.width)//2,42+(150-logo.height)//2),logo)
    folder=cfg['state']/'images';folder.mkdir(exist_ok=True)
    path=folder/(post_id+'.png');cover.save(path)
    db.execute('UPDATE posts SET image=? WHERE id=?',(str(path),post_id));db.commit()
    event(db,'image_generated',post_id)


def fresh_evidence(db, evidence):
    if not evidence:
        return False
    for e in evidence:
        doc=db.execute('SELECT * FROM documents WHERE url=?',(e['url'],)).fetchone()
        if not doc or doc['digest'] != e['digest'] or time.time()-doc['fetched_at']>86400:
            return False
    return True
