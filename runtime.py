"""Machine-local settings. This module never reads a media library."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CONFIG_FILE=ROOT/'local-config.json'
CONFIG=json.loads(CONFIG_FILE.read_text(encoding='utf-8-sig')) if CONFIG_FILE.is_file() else {}
chosen=Path(os.environ.get('MEMORY_DIRECTOR_DATA',CONFIG.get('data_dir',str(ROOT/'data')))).expanduser()
DATA_DIR=(chosen if chosen.is_absolute() else ROOT/chosen).resolve()
MODEL_DIR=DATA_DIR/'models'
WEB_PORT=int(CONFIG.get('web_port',7860))
LAN_PORT=int(CONFIG.get('lan_port',7861))
LAN_ENABLED=CONFIG.get('enable_lan',False) is True
VERSION='0.1.1'
if not (1024<=WEB_PORT<=65535 and 1024<=LAN_PORT<=65535 and WEB_PORT!=LAN_PORT):
    raise ValueError('请选择两个不同的 1024–65535 端口')
