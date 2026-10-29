"""Shareable status only: no media, database, logs, names, paths or IP addresses."""
import hashlib,json,platform,socket
from runtime import ROOT,MODEL_DIR,WEB_PORT,LAN_ENABLED,VERSION

def report():
    files=json.loads((ROOT/'models-manifest.json').read_text(encoding='utf-8'))
    # Hashing is limited to published model filenames, never a user's data files.
    verified=sum((MODEL_DIR/f['file']).is_file() and hashlib.sha256((MODEL_DIR/f['file']).read_bytes()).hexdigest()==f['sha256'] for f in files)
    with socket.socket() as sock:running=sock.connect_ex(('127.0.0.1',WEB_PORT))==0
    return {'version':VERSION,'python_major_minor':'.'.join(platform.python_version().split('.')[:2]),
            'public_model_files_verified':verified,'public_model_files_required':len(files),'local_port_in_use':running,'lan_enabled':LAN_ENABLED}
if __name__=='__main__':print(json.dumps(report(),ensure_ascii=False,indent=2))
