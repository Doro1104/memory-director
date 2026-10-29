"""Local media indexing, face grouping and memory-film jobs. No network calls."""
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import uuid
import random

import cv2
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
from curation import THEMES,TEMPLATES,curated_assets,music_catalog,filter_selected
from media_support import scan_live_photos,still_index,find_still

ASSET_COLUMNS = 'id,owner,name,path,thumb,kind,date,date_source,bytes,hash,shared,source'
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.3gp'}
from runtime import MODEL_DIR


def connection(data):
    conn = sqlite3.connect(Path(data) / 'memory.sqlite', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def migrate(conn, data):
    existing = {r[1] for r in conn.execute('PRAGMA table_info(assets)')}
    for name, definition in [('deleted_at', 'TEXT'), ('face_state', "TEXT DEFAULT 'pending'"), ('preview_state', "TEXT DEFAULT 'pending'"), ('purged_at','TEXT'),('live_parent','TEXT'),('live_checked','INTEGER DEFAULT 0'),('live_keys',"TEXT DEFAULT '[]'"),('note',"TEXT DEFAULT ''"),('playback_state',"TEXT DEFAULT 'pending'"),('playback_path','TEXT'),('playback_signature','TEXT'),('playback_error',"TEXT DEFAULT ''")]:
        if name not in existing:
            conn.execute(f'ALTER TABLE assets ADD COLUMN {name} {definition}')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id),name TEXT DEFAULT '',hidden INTEGER DEFAULT 0,embedding BLOB);
    CREATE TABLE IF NOT EXISTS faces(id TEXT PRIMARY KEY,asset TEXT REFERENCES assets(id),person TEXT REFERENCES people(id),crop TEXT,embedding BLOB);
    CREATE INDEX IF NOT EXISTS faces_person ON faces(person);
    CREATE INDEX IF NOT EXISTS faces_asset ON faces(asset);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id),kind TEXT,payload TEXT,status TEXT,done INTEGER DEFAULT 0,total INTEGER DEFAULT 0,message TEXT DEFAULT '',created REAL);
    CREATE TABLE IF NOT EXISTS films(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id),title TEXT,period TEXT,status TEXT,path TEXT,thumb TEXT,shared INTEGER DEFAULT 0,created REAL,notes TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS film_assets(film TEXT REFERENCES films(id),asset TEXT REFERENCES assets(id),PRIMARY KEY(film,asset));
    CREATE TABLE IF NOT EXISTS curation(asset TEXT PRIMARY KEY REFERENCES assets(id),value TEXT);
    CREATE TABLE IF NOT EXISTS music_tracks(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id),title TEXT,path TEXT,hash TEXT,bytes INTEGER,created REAL,UNIQUE(owner,hash));
    ''')
    if 'cover' not in {r[1] for r in conn.execute('PRAGMA table_info(people)')}:
        conn.execute('ALTER TABLE people ADD COLUMN cover TEXT')
    columns={r[1] for r in conn.execute('PRAGMA table_info(films)')}
    for name,definition in [('options',"TEXT DEFAULT '{}'"),('deleted_at','TEXT')]:
        if name not in columns:
            conn.execute(f'ALTER TABLE films ADD COLUMN {name} {definition}')
    conn.commit()
    for name in ('faces', 'films', 'jobs'):
        (Path(data) / name).mkdir(exist_ok=True)


def enqueue(conn, owner, kind, payload=None):
    existing = conn.execute("SELECT id FROM jobs WHERE owner=? AND kind=? AND status IN ('queued','running','paused')", (owner, kind)).fetchone()
    if existing and kind not in ('film','playback'):
        return existing['id']
    jid = uuid.uuid4().hex
    conn.execute('INSERT INTO jobs(id,owner,kind,payload,status,created) VALUES(?,?,?,?,?,?)',
                 (jid, owner, kind, json.dumps(payload or {}), 'queued', time.time()))
    conn.commit()
    return jid


def progress(conn, jid, done, total, message):
    conn.execute('UPDATE jobs SET done=?,total=?,message=? WHERE id=?', (done, total, message, jid))
    conn.commit()


def ffmpeg(args, timeout=120):
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-protocol_whitelist', 'file,pipe', '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
                            capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('视频文件无法解码或编码失败')


def video_metadata(path, thumb):
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-protocol_whitelist', 'file,pipe', '-hide_banner', '-i', str(path)],
                           capture_output=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = probe.stderr.decode('utf-8', errors='replace')
    if 'Video:' not in info:
        raise ValueError('不是可解码的视频')
    date, source = '', '日期未知'
    found = re.search(r'creation_time\s*:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)', info)
    if found:
        try:
            dt = datetime.fromisoformat(found[1].replace('Z', '+00:00')).astimezone()
            if 1900 <= dt.year <= datetime.now().year + 1:
                date, source = dt.strftime('%Y-%m-%d %H:%M:%S'), '视频拍摄元数据'
        except ValueError:
            pass
    ffmpeg(['-i', path, '-frames:v', '1', '-vf', 'scale=960:960:force_original_aspect_ratio=decrease', thumb], timeout=30)
    return date, source, 'Audio:' in info


def playback_signature(path):
    path=Path(path);stat=path.stat()
    return f'{path}|{stat.st_size}|{stat.st_mtime_ns}'


def prepare_playback(conn,data,job):
    aid=json.loads(job['payload'])['asset']
    asset=conn.execute("SELECT * FROM assets WHERE id=? AND owner=? AND deleted_at IS NULL AND purged_at IS NULL AND live_parent IS NULL",(aid,job['owner'])).fetchone()
    if not asset:
        raise ValueError('素材已移除，停止准备播放')
    signature=playback_signature(asset['path'])
    folder=Path(data)/'playback';folder.mkdir(exist_ok=True)
    temp=folder/(aid+'.part.mp4');target=folder/(aid+'.mp4')
    conn.execute("UPDATE assets SET playback_state='running',playback_error='' WHERE id=?",(aid,));conn.commit()
    try:
        ffmpeg(['-i',asset['path'],'-map','0:v:0','-map','0:a:0?','-sn','-dn',
                '-vf',"scale=w='min(1920,iw)':h='min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos",
                '-c:v','libx264','-preset','fast','-crf','19','-threads','2','-pix_fmt','yuv420p',
                '-c:a','aac','-b:a','160k','-ac','2','-movflags','+faststart',temp],timeout=1800)
        if playback_signature(asset['path'])!=signature:
            raise ValueError('原文件发生变化，请重新准备播放')
        current=conn.execute('SELECT deleted_at,purged_at,live_parent FROM assets WHERE id=?',(aid,)).fetchone()
        if not current or current['deleted_at'] or current['purged_at'] or current['live_parent']:
            raise ValueError('素材已移除，停止准备播放')
        temp.replace(target)
        conn.execute("UPDATE assets SET playback_state='done',playback_path=?,playback_signature=?,playback_error='' WHERE id=?",(str(target),signature,aid));conn.commit()
        # Only viewed videos get a cache. Keep older reusable copies within about 4 GB.
        cached=sorted(folder.glob('*.mp4'),key=lambda p:p.stat().st_mtime)
        size=sum(p.stat().st_size for p in cached)
        for path in cached:
            if size<=4*1024**3:break
            if path==target or path.name.endswith('.part.mp4'):continue
            try:
                length=path.stat().st_size;path.unlink();size-=length
                conn.execute("UPDATE assets SET playback_state='pending',playback_path=NULL WHERE playback_path=?",(str(path),))
            except OSError:continue
        conn.commit()
    except Exception as error:
        conn.execute("UPDATE assets SET playback_state='error',playback_error=? WHERE id=?",(str(error)[:180],aid));conn.commit()
        raise
    finally:
        temp.unlink(missing_ok=True)


def import_videos(conn, data, job, legacy):
    root = legacy.parent
    mismatch = {}
    manifest = legacy / '扩展名错配清单.csv'
    if manifest.exists():
        with manifest.open(encoding='utf-8-sig', newline='') as f:
            mismatch = {str((root / r['relative']).resolve()).casefold(): r['kind'] for r in csv.DictReader(f)}
    # Only known photo backup directories, not contacts, messages, audio or the generated gallery.
    roots = [p for p in root.iterdir() if p.is_dir() and '照片' in p.name and p != legacy and not p.name.startswith('_')]
    candidates = []
    for folder in roots:
        for p in folder.rglob('*'):
            if not p.is_file() or p.is_symlink():
                continue
            actual = mismatch.get(str(p.resolve()).casefold())
            if actual == 'video' or (actual is None and p.suffix.lower() in VIDEO_EXT):
                candidates.append(p)
    paths = {r[0] for r in conn.execute('SELECT path FROM assets')}
    stills=still_index(conn,legacy,job['owner'])
    added = skipped = failed = 0
    for i, path in enumerate(candidates):
        if conn.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'paused':
            return False
        aid = 'video-' + hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()[:24]
        if str(path.resolve()) in paths:
            skipped += 1
        else:
            try:
                date, source = '', '日期未知'
                month = re.search(r'(20\d{2})(0[1-9]|1[0-2])(?:_|[/\\])', str(path))
                if month:
                    date, source = month[1] + '-' + month[2], '备份目录月份（待确认）'
                # Preserve a playable extension for historically misnamed video files.
                name = path.name if path.suffix.lower() in VIDEO_EXT else path.stem + '.mp4'
                conn.execute(f'INSERT OR IGNORE INTO assets({ASSET_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (aid, job['owner'], name, str(path.resolve()), None, 'video', date, source, path.stat().st_size, None, 0, 'legacy-video'))
                try:
                    parent=find_still(conn,{'path':str(path),'owner':job['owner'],'source':'legacy-video'},stills)
                except (OSError,ValueError):
                    parent=None
                conn.execute('UPDATE assets SET live_parent=?,live_checked=1 WHERE id=?',(parent,aid))
                added += 1
            except (OSError, ValueError, subprocess.TimeoutExpired):
                failed += 1
        if (i+1)%100 == 0 or i+1 == len(candidates):
            progress(conn, job['id'], i+1, len(candidates), f'新增 {added} 个视频 · 已接入 {skipped} 个 · 不可用 {failed} 个；封面与拍摄时间后台提取')
    enqueue(conn,job['owner'],'posters')
    return True


def video_posters(conn,data,job):
    rows=conn.execute("SELECT * FROM assets WHERE owner=? AND kind='video' AND deleted_at IS NULL AND live_parent IS NULL AND preview_state='pending' ORDER BY date DESC LIMIT 30",(job['owner'],)).fetchall()
    total=conn.execute("SELECT count(*) FROM assets WHERE owner=? AND kind='video' AND deleted_at IS NULL AND live_parent IS NULL",(job['owner'],)).fetchone()[0]
    done=conn.execute("SELECT count(*) FROM assets WHERE owner=? AND kind='video' AND deleted_at IS NULL AND live_parent IS NULL AND preview_state!='pending'",(job['owner'],)).fetchone()[0]
    for row in rows:
        if conn.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='paused':
            return False
        thumb=Path(data)/'thumbs'/(row['id']+'.jpg')
        try:
            date,source,_=video_metadata(row['path'],thumb)
            conn.execute("UPDATE assets SET thumb=?,date=?,date_source=?,preview_state='done',face_state='pending' WHERE id=?",(str(thumb),date or row['date'],source if date else row['date_source'],row['id']))
        except (OSError,ValueError,subprocess.TimeoutExpired):
            conn.execute("UPDATE assets SET preview_state='error',face_state='error' WHERE id=?",(row['id'],))
        done+=1
        progress(conn,job['id'],done,total,f'已提取 {done:,} / {total:,} 个视频的封面与拍摄时间')
    if rows:
        enqueue(conn,job['owner'],'faces')
    return not conn.execute("SELECT 1 FROM assets WHERE owner=? AND kind='video' AND deleted_at IS NULL AND live_parent IS NULL AND preview_state='pending' LIMIT 1",(job['owner'],)).fetchone()


def group_faces(conn, owner, aid, samples, data):
    # Conservative matching; labels are suggestions and remain editable by the owner.
    people = conn.execute('SELECT id,embedding FROM people WHERE owner=? AND hidden=0', (owner,)).fetchall()
    vectors = [np.frombuffer(p['embedding'], dtype=np.float32) for p in people]
    for crop, embedding in samples:
        embedding = embedding.astype(np.float32).reshape(-1)
        embedding /= max(float(np.linalg.norm(embedding)), 1e-8)
        scores = np.stack(vectors) @ embedding if vectors else np.array([])
        best = int(scores.argmax()) if len(scores) else -1
        if best >= 0 and scores[best] >= 0.50:
            person = people[best]['id']
        else:
            person = uuid.uuid4().hex
            conn.execute('INSERT INTO people(id,owner,embedding) VALUES(?,?,?)', (person, owner, embedding.tobytes()))
            people.append({'id':person})
            vectors.append(embedding)
        fid = uuid.uuid4().hex
        target = Path(data) / 'faces' / (fid + '.jpg')
        crop.save(target, quality=88)
        conn.execute('INSERT INTO faces VALUES(?,?,?,?,?)', (fid, aid, person, str(target), embedding.tobytes()))


def scan_faces(conn, data, job):
    # Load bytes in Python so OpenCV also works under Chinese Windows paths.
    detector = cv2.FaceDetectorYN.create('onnx', np.frombuffer((MODEL_DIR / 'face_detection_yunet_2023mar.onnx').read_bytes(),np.uint8), np.array([],np.uint8), (640,640), 0.9, 0.3, 5000)
    recognizer = cv2.FaceRecognizerSF.create('onnx', np.frombuffer((MODEL_DIR / 'face_recognition_sface_2021dec.onnx').read_bytes(),np.uint8), np.array([],np.uint8))
    cv2.setNumThreads(2)
    rows = conn.execute("SELECT * FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND face_state='pending' AND (kind='image' OR thumb IS NOT NULL) ORDER BY date DESC LIMIT 120", (job['owner'],)).fetchall()
    total = conn.execute("SELECT count(*) FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL", (job['owner'],)).fetchone()[0]
    done = conn.execute("SELECT count(*) FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND face_state!='pending'", (job['owner'],)).fetchone()[0]
    for row in rows:
        if conn.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0] == 'paused':
            return False
        state = 'done'
        try:
            with Image.open(row['thumb'] or row['path']) as im:
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.thumbnail((960,960))
                frame = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)
                height, width = frame.shape[:2]
                detector.setInputSize((width,height))
                _, faces = detector.detect(frame)
                samples = []
                for face in faces if faces is not None else []:
                    x,y,w,h = face[:4]
                    if min(w,h) < 32:
                        continue
                    embedding = recognizer.feature(recognizer.alignCrop(frame,face)).copy()
                    pad = max(w,h)*0.2
                    crop = im.crop((max(0,int(x-pad)),max(0,int(y-pad)),min(width,int(x+w+pad)),min(height,int(y+h+pad))))
                    crop.thumbnail((240,240))
                    samples.append((crop,embedding))
                conn.execute('BEGIN IMMEDIATE')
                active = conn.execute('SELECT deleted_at FROM assets WHERE id=?', (row['id'],)).fetchone()
                if active and active[0] is None:
                    group_faces(conn,job['owner'],row['id'],samples,data)
                conn.commit()
        except (OSError, ValueError, cv2.error):
            conn.rollback()
            state = 'error'
        conn.execute('UPDATE assets SET face_state=? WHERE id=?', (state,row['id']))
        done += 1
        progress(conn,job['id'],done,total,f'已检查 {done:,} / {total:,} 个素材 · 人物分组可手动修正')
    remains = conn.execute("SELECT 1 FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND face_state='pending' AND (kind='image' OR thumb IS NOT NULL) LIMIT 1", (job['owner'],)).fetchone()
    return not remains


def title_image(path, title, subtitle, size=(1280,720)):
    image = Image.new('RGB',size,'#18251f')
    draw = ImageDraw.Draw(image)
    scale=size[0]/1280
    fontpath = Path('C:/Windows/Fonts/msyh.ttc')
    font = ImageFont.truetype(str(fontpath),int(48*scale)) if fontpath.exists() else ImageFont.load_default()
    small = ImageFont.truetype(str(fontpath),int(22*scale)) if fontpath.exists() else font
    draw.line(tuple(int(v*scale) for v in (560,235,720,235)),fill='#c6ac7a',width=2)
    draw.text((640*scale,330*scale),title[:26],font=font,fill='#f5f0e5',anchor='mm')
    draw.text((640*scale,420*scale),subtitle,font=small,fill='#b9c6b7',anchor='mm')
    draw.text((640*scale,630*scale),'记忆导演 · 把日子，留成故事',font=small,fill='#91a58e',anchor='mm')
    image.save(path)


def film_candidates(conn,owner,period):
    return conn.execute("SELECT * FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND date LIKE ? ORDER BY date,id", (owner,period+'%')).fetchall()


class FilmCancelled(Exception):
    pass


def check_film_active(conn,fid):
    row=conn.execute('SELECT deleted_at FROM films WHERE id=?',(fid,)).fetchone()
    if not row or row['deleted_at']:
        raise FilmCancelled('影片已删除，制作已取消')


def render_film(conn,data,job):
    payload=json.loads(job['payload']);fid=payload['film']
    film=conn.execute('SELECT * FROM films WHERE id=?',(fid,)).fetchone()
    check_film_active(conn,fid)
    options=json.loads(film['options'] or '{}')
    conn.execute("UPDATE films SET status='running' WHERE id=?",(fid,));conn.commit()
    ids=options.get('ids')
    if not ids:
        suggestions,_=curated_assets(conn,job['owner'],film['period'],options.get('theme','people'),options.get('person'),prompt=options.get('prompt',''),story_mode=options.get('story_mode','event'))
        ids=[r['id'] for r in suggestions]
    selected=[]
    for aid in ids:
        row=conn.execute('SELECT * FROM assets WHERE id=? AND owner=? AND deleted_at IS NULL AND live_parent IS NULL AND purged_at IS NULL',(aid,job['owner'])).fetchone()
        if row and row['date'].startswith(film['period']):
            selected.append(row)
    selected.sort(key=lambda r:r['date'])
    selected,screened=filter_selected(conn,selected)
    if len(selected)<2:
        raise ValueError('主题下可用素材不足两份，请调整主题、时期或先在总图库珍藏素材')
    width,height=(1920,1080) if options.get('resolution','1080')=='1080' else (1280,720)
    template=TEMPLATES[options.get('template','warm')];seconds=template['seconds']
    folder=Path(data)/'jobs'/job['id'];folder.mkdir(exist_ok=True)
    cover=folder/'title.jpg';title_image(cover,film['title'],film['period']+' · '+THEMES.get(options.get('theme'),'主题回忆'),(width,height))
    silent=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    fit=f'scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x18251f,setsar=1,fps=30'
    encode=['-c:v','libx264','-preset','fast','-crf','18','-threads','2','-pix_fmt','yuv420p','-color_range','tv','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-movflags','+faststart']
    clips=[];used=[];failed=sum(screened.values())
    title_clip=folder/'000.mp4'
    ffmpeg(['-loop','1','-i',cover,*silent,'-map','0:v:0','-map','1:a:0','-vf',fit+',fade=t=in:st=0:d=0.4,fade=t=out:st=1.7:d=0.3','-t','2',*encode,title_clip])
    clips.append(title_clip)
    for i,asset in enumerate(selected):
        check_film_active(conn,fid)
        if conn.execute('SELECT deleted_at FROM assets WHERE id=?',(asset['id'],)).fetchone()[0] is not None:
            continue
        clip=folder/f'{i+1:03}.mp4'
        try:
            if asset['kind']=='image':
                prepared=folder/f'{i+1:03}.png'
                with Image.open(asset['path']) as im:
                    im=ImageOps.exif_transpose(im).convert('RGB');im.thumbnail((3840,3840));im.save(prepared)
                # A slow push-in and gentle fades give still photographs a little movement.
                vf=fit+f",zoompan=z='min(zoom+0.00015,1.018)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps=30,"+template['color']+f'fade=t=in:st=0:d=0.3,fade=t=out:st={seconds-.3}:d=0.3'
                args=['-loop','1','-i',prepared,*silent,'-map','0:v:0','-map','1:a:0','-vf',vf,'-t',str(seconds)]
            else:
                probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-protocol_whitelist', 'file,pipe','-hide_banner','-i',asset['path']],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                audio=b'Audio:' in probe.stderr
                args=['-i',asset['path']]+([] if audio else silent)+['-map','0:v:0','-map','0:a:0' if audio else '1:a:0','-vf',fit+','+template['color']+'fade=t=in:st=0:d=0.2','-af','apad','-t',str(seconds)]
            ffmpeg([*args,*encode,clip])
            clips.append(clip);used.append(asset['id'])
        except (OSError,ValueError,subprocess.TimeoutExpired):
            failed+=1
        progress(conn,job['id'],i+1,len(selected),f'正在制作 {i+1} / {len(selected)} · 跳过不可用素材 {failed} 个')
    if len(used)<2:
        raise ValueError('可用素材不足两份，请检查原片是否在线')
    listing=folder/'clips.txt';listing.write_text('\n'.join("file '"+c.name+"'" for c in clips),encoding='utf-8')
    dest=Path(data)/'films'/(fid+'.mp4');poster=Path(data)/'films'/(fid+'.jpg')
    joined=folder/'joined.mp4'
    ffmpeg(['-f','concat','-safe','0','-i',listing,'-c','copy',joined])
    music=music_catalog(data,job['owner']);choice=options.get('music','random')
    track=random.choice(music) if choice=='random' and music else next((r for r in music if r['id']==choice),None)
    if choice!='none' and not track:
        raise ValueError('本地配乐库尚未安装，请选择不配乐或安装配乐库')
    if track:
        # Loop the licensed instrumental bed, retain speech/original sound and fade the ending.
        probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-protocol_whitelist', 'file,pipe','-hide_banner','-i',str(joined)],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        match=re.search(rb'Duration: (\d+):(\d+):([\d.]+)',probe.stderr)
        duration=int(match[1])*3600+int(match[2])*60+float(match[3]) if match else 2+len(used)*seconds
        mix=f'[1:a]volume=0.20,afade=t=in:st=0:d=1.5,afade=t=out:st={max(0,duration-2)}:d=2[bg];[0:a]volume=0.85[original];[original][bg]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]'
        ffmpeg(['-i',joined,'-stream_loop','-1','-i',Path(data)/'music'/track['file'],'-filter_complex',mix,'-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',dest])
    else:
        ffmpeg(['-i',joined,'-c','copy','-movflags','+faststart',dest])
    try:
        check_film_active(conn,fid)
    except FilmCancelled:
        dest.unlink(missing_ok=True)
        raise
    with Image.open(cover) as im:
        im.thumbnail((640,360));im.save(poster)
    conn.execute('DELETE FROM film_assets WHERE film=?',(fid,))
    conn.executemany('INSERT INTO film_assets VALUES(?,?)',[(fid,aid) for aid in used])
    notes=f'{width}×{height} · {template["name"]} · {THEMES.get(options.get("theme"),"主题回忆")} · 收录 {len(used)} 个片刻，跳过 {failed} 个。配乐：'+(track['title']+('（CC0）' if track['license']=='CC0-1.0' else '（本地上传）') if track else '无')
    notes+='。已检查明显闭眼、相似去重，按拍摄时间排序。'
    if options.get('prompt'):notes+='导演要求：'+options['prompt'][:100]
    changed=conn.execute("UPDATE films SET status='done',path=?,thumb=?,notes=? WHERE id=? AND deleted_at IS NULL",(str(dest),str(poster),notes,fid))
    if not changed.rowcount:
        conn.rollback();dest.unlink(missing_ok=True);poster.unlink(missing_ok=True)
        raise FilmCancelled('影片已删除，制作已取消')
    conn.commit()
    progress(conn,job['id'],len(selected),len(selected),notes)
    # Remove only the temporary files this render produced, keeping the result and database.
    for item in folder.iterdir():
        if item.is_file():
            item.unlink()
    folder.rmdir()


def start_worker(data,legacy):
    def run():
        conn=connection(data)
        conn.execute("UPDATE jobs SET status='queued' WHERE status='running'");conn.commit()
        for row in conn.execute("SELECT DISTINCT owner FROM assets WHERE kind='video' AND live_checked=0 AND purged_at IS NULL").fetchall():
            enqueue(conn,row['owner'],'live')
        while True:
            job=conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY CASE kind WHEN 'playback' THEN 0 WHEN 'film' THEN 1 WHEN 'videos' THEN 2 WHEN 'live' THEN 3 ELSE 4 END,created LIMIT 1").fetchone()
            if not job:
                time.sleep(2);continue
            conn.execute("UPDATE jobs SET status='running' WHERE id=?",(job['id'],));conn.commit()
            try:
                if job['kind']=='playback':
                    prepare_playback(conn,data,job);complete=True
                elif job['kind']=='faces':
                    complete=scan_faces(conn,data,job)
                elif job['kind']=='videos':
                    complete=import_videos(conn,data,job,legacy)
                elif job['kind']=='posters':
                    complete=video_posters(conn,data,job)
                elif job['kind']=='live':
                    complete=scan_live_photos(conn,data,job,legacy)
                else:
                    render_film(conn,data,job);complete=True
                conn.execute("UPDATE jobs SET status=? WHERE id=? AND status NOT IN ('paused','cancelled')",('done' if complete else 'queued',job['id']))
                if not complete:
                    conn.execute('UPDATE jobs SET created=? WHERE id=?',(time.time(),job['id']))
                conn.commit()
            except FilmCancelled:
                conn.rollback()
                conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(job['id'],));conn.commit()
            except Exception as error:
                conn.rollback()
                message=str(error)[:240]
                conn.execute("UPDATE jobs SET status='error',message=? WHERE id=?",(message,job['id']))
                if job['kind']=='film':
                    conn.execute("UPDATE films SET status='error',notes=? WHERE id=?",(message,json.loads(job['payload'])['film']))
                conn.commit()
    thread=threading.Thread(target=run,daemon=True,name='memory-processing');thread.start()
    return thread
