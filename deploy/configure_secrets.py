#!/usr/bin/env python3
"""Run interactively ON THE SERVER. Never send the values to an agent/chat."""
import getpass
import os
from pathlib import Path

root=Path(__file__).resolve().parent.parent
dest=root/'.env'
if dest.exists():
    raise SystemExit('.env already exists. Edit locally; this command will not overwrite existing secrets.')
if not os.isatty(0):
    raise SystemExit('Run this from an interactive terminal; token input must be hidden.')
token=getpass.getpass('Telegram bot token (hidden): ').strip()
if ':' not in token or any(c.isspace() for c in token):
    raise SystemExit('Invalid token format')
channel=input('Channel [@a_bank_wallet]: ').strip() or '@a_bank_wallet'
if not channel.startswith('@') or not channel[1:].replace('_','').isalnum():
    raise SystemExit('Provide a public @channel username')
key=getpass.getpass('OpenAI API key (hidden; Enter to configure later): ').strip()
if any(c.isspace() for c in key):raise SystemExit('Invalid key format')
fd=os.open(str(dest),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:
    f.write('TELEGRAM_BOT_TOKEN='+token+'\nTELEGRAM_CHANNEL='+channel+'\nOPENAI_API_KEY='+key+'\nOPENAI_TEXT_MODEL=gpt-4.1-mini\nOPENAI_IMAGE_MODEL=gpt-image-1\n')
print('Configuration saved with mode 0600. No secret values printed.')
