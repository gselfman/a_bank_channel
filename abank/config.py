import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load():
    cfg = json.loads((ROOT / 'config.json').read_text())
    for key in ('TELEGRAM_BOT_TOKEN', 'OPENAI_API_KEY', 'OPENAI_TEXT_MODEL', 'OPENAI_IMAGE_MODEL', 'TELEGRAM_CHANNEL'):
        if os.environ.get(key):
            cfg[key] = os.environ[key]
    cfg['root'] = ROOT
    cfg['state'] = Path(os.environ.get('ABANK_STATE_DIR', str(ROOT / 'state')))
    cfg['state'].mkdir(parents=True, exist_ok=True)
    cfg['TELEGRAM_CHANNEL'] = cfg.get('TELEGRAM_CHANNEL', '@a_bank_wallet')
    return cfg
