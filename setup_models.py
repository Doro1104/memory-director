"""Download only allowlisted public artifacts, with SHA-256 verification."""
import argparse,hashlib,json,shutil,urllib.request
from pathlib import Path
from runtime import ROOT,DATA_DIR,MODEL_DIR


def install(include_music=False):
    entries=json.loads((ROOT/'models-manifest.json').read_text(encoding='utf-8'))
    if include_music:
        for track in json.loads((ROOT/'music-library.json').read_text(encoding='utf-8')):
            entries.append({'file':'../music/'+track['file'],'url':track['download'],'sha256':track['sha256']})
    for item in entries:
        target=(MODEL_DIR/item['file']).resolve()
        if not target.is_relative_to(DATA_DIR):raise ValueError('模型清单路径越界')
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']:
            print('verified',item['file'],flush=True);continue
        temp=target.with_suffix('.download')
        try:
            with urllib.request.urlopen(item['url'],timeout=60) as source,temp.open('wb') as output:
                shutil.copyfileobj(source,output)
            if hashlib.sha256(temp.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('校验失败：'+item['file'])
            temp.replace(target);print('installed',item['file'],flush=True)
        finally:temp.unlink(missing_ok=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--music',action='store_true');args=parser.parse_args();install(args.music)
