"""Launch this installation, without attaching to an unrelated occupied port."""
from pathlib import Path
import socket,subprocess,sys,time,webbrowser,json,urllib.request
from runtime import ROOT,WEB_PORT,CONFIG

with socket.socket() as probe:
    occupied=probe.connect_ex(('127.0.0.1',WEB_PORT))==0
if occupied:
    try:
        with urllib.request.urlopen(f'http://localhost:{WEB_PORT}/api/health',timeout=2) as response:identity=json.load(response)
        if not CONFIG.get('install_id') or identity.get('instance')!=CONFIG['install_id']:raise ValueError('different installation')
        webbrowser.open(f'http://localhost:{WEB_PORT}');raise SystemExit(0)
    except (OSError,ValueError):raise SystemExit('本机端口已被另一服务占用，请修改 local-config.json 的 web_port。')
logs=ROOT/'work';logs.mkdir(exist_ok=True)
with (logs/'server.log').open('a',encoding='utf-8') as log:
    subprocess.Popen([sys.executable,str(ROOT/'server.py')],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
for _ in range(60):
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',WEB_PORT))==0:break
    time.sleep(.5)
else:raise SystemExit('服务未启动，请在本机检查 work/server.log；不要直接把日志提交给 Agent 或 GitHub。')
webbrowser.open(f'http://localhost:{WEB_PORT}')
