"""Full-resolution browser images and conservative Live Photo pairing."""
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading

import imageio_ffmpeg
from PIL import Image,ImageOps

_display_lock=threading.Lock()
UUID=re.compile(rb'[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}')
KEY=b'com.apple.quicktime.content.identifier'


def movie_identifier(path):
    path=Path(path)
    if path.suffix.lower() not in ('','.mov','.mp4','.m4v'):
        return None
    with path.open('rb') as f:
        size=path.stat().st_size
        f.seek(max(0,size-262144));tail=f.read()
        f.seek(0);head=f.read(min(131072,size))
    for block in (tail,head):
        position=block.find(KEY)
        if position>=0:
            # QuickTime stores the content identifier value after its metadata key table.
            values=UUID.findall(block[position:position+16384])
            if values:
                return values[0].decode('ascii').upper()
    return None


def image_identifiers(path):
    # Apple stores the ASCII asset UUIDs in the small Exif/MakerNote region.
    # Reading that region avoids decoding thousands of HEIC photographs just to pair them.
    path=Path(path)
    with path.open('rb') as f:
        header=f.read(65536)
        values=UUID.findall(header)
        if values:
            return {v.decode('ascii').upper() for v in values}
    with Image.open(path) as image:
        try:
            exif=image.getexif()
            note=exif.get_ifd(34665).get(37500,b'')
        except (ValueError,TypeError,KeyError):
            return set()
        return {v.decode('ascii').upper() for v in UUID.findall(note)} if isinstance(note,bytes) else set()


def live_photo_pair(movie,image):
    identifier=movie_identifier(movie)
    return bool(identifier and identifier in image_identifiers(image))


def still_index(conn,legacy,owner):
    images=conn.execute("SELECT id,name,path,date,live_keys,live_checked FROM assets WHERE owner=? AND kind='image' AND live_parent IS NULL AND deleted_at IS NULL AND purged_at IS NULL",(owner,)).fetchall()
    by_id={r['id']:r for r in images};by_source={}
    manifest=Path(legacy)/'照片总清单.csv'
    if manifest.is_file():
        with manifest.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                asset=by_id.get('legacy-'+row['id'])
                if asset:
                    original=Path(legacy).parent/row['relative']
                    by_source[(str(original.parent).casefold(),original.stem.casefold())]=asset
    for number,image in enumerate(images,1):
        path=Path(image['path'])
        by_source[(str(path.parent).casefold(),path.stem.casefold())]=image
        if not image['live_checked']:
            try:
                keys=sorted(image_identifiers(path))
                conn.execute('UPDATE assets SET live_keys=?,live_checked=1 WHERE id=?',(json.dumps(keys),image['id']))
            except (OSError,ValueError):
                continue
        if number%300==0:
            conn.commit()
    conn.commit()
    return by_source


def find_still(conn,video,index):
    path=Path(video['path'])
    image=index.get((str(path.parent).casefold(),path.stem.casefold()))
    if image and live_photo_pair(path,image['path']):
        return image['id']
    # Renamed/deduplicated originals and uploads can have different filenames.
    # Only an exact embedded identifier establishes the photo pairing.
    identifier=movie_identifier(path)
    if identifier:
        for row in conn.execute("SELECT id,live_keys FROM assets WHERE owner=? AND kind='image' AND live_parent IS NULL AND deleted_at IS NULL AND purged_at IS NULL AND live_keys!='[]' ORDER BY (source='live-still')",(video['owner'],)):
            if identifier in json.loads(row['live_keys']):
                return row['id']
    return None


def recover_live_still(conn,data,video,identifier):
    """Keep a viewable still when a backup contains only the Live Photo movie."""
    aid='live-still-'+hashlib.sha256(video['id'].encode()).hexdigest()[:24]
    if conn.execute('SELECT 1 FROM assets WHERE id=?',(aid,)).fetchone():
        return aid
    data=Path(data);target=data/'uploads'/(aid+'.jpg');thumb=data/'thumbs'/(aid+'.jpg')
    result=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-protocol_whitelist','file,pipe','-hide_banner','-loglevel','error','-y','-i',video['path'],
                           '-map','0:v:0','-vf','thumbnail','-frames:v','1','-q:v','2',str(target)],
                          capture_output=True,timeout=60,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not target.is_file():
        target.unlink(missing_ok=True)
        raise ValueError('Live Photo 画面无法读取，原文件保留')
    with Image.open(target) as image:
        ImageOps.fit(image.convert('RGB'),(440,330)).save(thumb,quality=84)
    conn.execute('''INSERT INTO assets(id,owner,name,path,thumb,kind,date,date_source,bytes,shared,source,live_keys,live_checked,note,deleted_at)
                    VALUES(?,?,?,?,?,'image',?,?,?,?, 'live-still',?,1,?,?)''',
                 (aid,video['owner'],Path(video['name']).stem+'.jpg',str(target),str(thumb),video['date'],
                  'Live Photo 提取画面（静态原图缺失）',target.stat().st_size,video['shared'],json.dumps([identifier]),video['note'],video['deleted_at']))
    conn.execute('INSERT OR IGNORE INTO favorites(user,asset) SELECT user,? FROM favorites WHERE asset=?',(aid,video['id']))
    return aid


def scan_live_photos(conn,data,job,legacy):
    owner=job['owner'];index=still_index(conn,legacy,owner)
    rows=conn.execute("SELECT * FROM assets WHERE owner=? AND kind='video' AND live_checked=0 AND purged_at IS NULL ORDER BY id LIMIT 300",(owner,)).fetchall()
    total=conn.execute("SELECT count(*) FROM assets WHERE owner=? AND kind='video' AND purged_at IS NULL",(owner,)).fetchone()[0]
    done=conn.execute("SELECT count(*) FROM assets WHERE owner=? AND kind='video' AND live_checked=1 AND purged_at IS NULL",(owner,)).fetchone()[0]
    for row in rows:
        if conn.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]=='paused':
            return False
        try:
            parent=find_still(conn,row,index)
            if not parent:
                identifier=movie_identifier(row['path'])
                if identifier:
                    parent=recover_live_still(conn,data,row,identifier)
            if parent and row['live_parent'] and row['live_parent']!=parent:
                conn.execute("UPDATE assets SET live_parent=? WHERE id=? AND source='live-still'",(parent,row['live_parent']))
            conn.execute('UPDATE assets SET live_parent=?,live_checked=1 WHERE id=?',(parent,row['id']))
        except (OSError,ValueError,subprocess.TimeoutExpired):
            # Do not hide an unverified or offline asset. A later scan can retry it.
            conn.execute('UPDATE assets SET live_checked=1 WHERE id=?',(row['id'],))
        done+=1
        if done%30==0 or row==rows[-1]:
            hidden=conn.execute('SELECT count(*) FROM assets WHERE owner=? AND live_parent IS NOT NULL',(owner,)).fetchone()[0]
            conn.execute('UPDATE jobs SET done=?,total=?,message=? WHERE id=?',(done,total,f'已核对 {done:,} / {total:,} 个视频，合并 {hidden:,} 份 Live Photo 伴随短片；原文件保留',job['id']))
            conn.commit()
    return not conn.execute("SELECT 1 FROM assets WHERE owner=? AND kind='video' AND live_checked=0 AND purged_at IS NULL LIMIT 1",(owner,)).fetchone()


def display_image(asset,data):
    """Return original web-native bytes, or a bounded, reusable full-size HEIC JPEG cache."""
    source=Path(asset['path'])
    if not source.is_file():
        raise FileNotFoundError('原片不在线，请连接素材硬盘')
    with Image.open(source) as header:
        if header.format in ('JPEG','PNG','WEBP','GIF','BMP'):
            return source,Image.MIME.get(header.format)
    directory=Path(data)/'display';directory.mkdir(exist_ok=True)
    target=directory/(asset['id']+'.jpg');record=directory/(asset['id']+'.json')
    stat=source.stat();signature=f'{source}|{stat.st_size}|{stat.st_mtime_ns}'
    with _display_lock:
        if not (target.exists() and record.exists() and record.read_text(encoding='utf-8')==signature):
            temp=directory/(asset['id']+'.part')
            try:
                with Image.open(source) as image:
                    profile=image.info.get('icc_profile')
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    image.save(temp,format='JPEG',quality=95,subsampling=0,**({'icc_profile':profile} if profile else {}))
                temp.replace(target);record.write_text(signature,encoding='utf-8')
            finally:
                temp.unlink(missing_ok=True)
        target.touch()
        files=sorted(directory.glob('*.jpg'),key=lambda p:p.stat().st_mtime)
        size=sum(p.stat().st_size for p in files)
        for cached in files:
            if size<=512*1024*1024:
                break
            if cached==target:
                continue
            try:
                size-=cached.stat().st_size;cached.unlink();cached.with_suffix('.json').unlink(missing_ok=True)
            except OSError:
                continue
    return target,'image/jpeg'
