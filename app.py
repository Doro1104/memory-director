import csv
import hashlib
import io
import ipaddress
import json
from pathlib import Path
from runtime import DATA_DIR
import re
import secrets
import socket
import sqlite3
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, abort, g, jsonify, request, send_file, session
from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener
import qrcode
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer,BadSignature
from processing import ASSET_COLUMNS, MODEL_DIR, enqueue, migrate, start_worker, video_metadata,ffmpeg,playback_signature
from curation import THEMES,TEMPLATES,curated_assets,music_catalog,filter_selected
from deletion import select_assets,build_plan,purge_assets
from media_support import display_image,image_identifiers,find_still

register_heif_opener()
ROOT = Path(__file__).resolve().parent
LEGACY = DATA_DIR / 'legacy-disabled'
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.bmp', '.tif', '.tiff'}
VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.3gp'}


def create_app(data_dir=None):
    app = Flask(__name__, static_folder='static', static_url_path='/static')
    data = Path(data_dir or DATA_DIR).resolve()
    for name in ('uploads', 'thumbs', 'temp'):
        (data / name).mkdir(parents=True, exist_ok=True)
    keyfile = data / 'secret.key'
    if not keyfile.exists():
        keyfile.write_text(secrets.token_hex(32), encoding='ascii')
    app.config.update(SECRET_KEY=keyfile.read_text(), MAX_CONTENT_LENGTH=4 * 1024**3,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      PERMANENT_SESSION_LIFETIME=timedelta(days=7), DATA_DIR=data)

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(data / 'memory.sqlite', timeout=30)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop('db', None)
        if conn:
            conn.close()

    with app.app_context():
        db().executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,password TEXT NOT NULL,admin INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,path TEXT UNIQUE NOT NULL,thumb TEXT,kind TEXT NOT NULL,date TEXT NOT NULL,date_source TEXT NOT NULL,bytes INTEGER NOT NULL,hash TEXT,shared INTEGER DEFAULT 0,source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS asset_date ON assets(date DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS owner_hash ON assets(owner,hash) WHERE hash IS NOT NULL;
        CREATE TABLE IF NOT EXISTS favorites(user TEXT REFERENCES users(id),asset TEXT REFERENCES assets(id),PRIMARY KEY(user,asset));
        CREATE TABLE IF NOT EXISTS attempts(ip TEXT PRIMARY KEY,count INTEGER,last REAL);
        ''')
        db().commit()
        migrate(db(), data)

    def current():
        return db().execute('SELECT id,name,admin FROM users WHERE id=?', (session.get('uid'),)).fetchone()

    def login_required(fn):
        @wraps(fn)
        def wrapped(*a, **kw):
            g.user = current()
            if not g.user:
                abort(401, '请先登录')
            return fn(*a, **kw)
        return wrapped

    @app.before_request
    def protect():
        # Reject DNS rebinding and cross-site form/JSON requests. No CORS is enabled.
        host = request.host.split(':')[0].strip('[]')
        try:
            ip = ipaddress.ip_address(host)
            allowed = ip.is_private or ip.is_loopback
        except ValueError:
            allowed = host == 'localhost'
        if not allowed:
            abort(403, '请使用本机或局域网 IP 地址访问')
        if request.method in ('POST', 'PATCH', 'DELETE'):
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                abort(403, '请求来源不匹配')
            if request.path not in ('/api/login', '/api/setup'):
                if not session.get('csrf') or not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                    abort(403, '页面已失效，请刷新后重试')
            elif not request.is_json:
                abort(415, '需要 JSON 请求')

    @app.after_request
    def headers(resp):
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'DENY'
        resp.headers['Referrer-Policy'] = 'same-origin'
        resp.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.path == '/' or request.path.startswith(('/api/', '/media/', '/static/')):
            resp.headers['Cache-Control'] = 'no-store'
        return resp

    @app.errorhandler(Exception)
    def errors(error):
        from werkzeug.exceptions import HTTPException
        if isinstance(error, HTTPException):
            return jsonify(error=error.description), error.code
        app.logger.error('Request failed: %s',type(error).__name__)
        return jsonify(error='操作未完成，请查看电脑端日志后重试'), 500

    @app.get('/')
    def index():
        return send_file(ROOT / 'static/index.html')

    @app.get('/sw.js')
    def web_worker():
        response = send_file(ROOT / 'static/sw.js', mimetype='text/javascript')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/api/reachability')
    def reachability():
        return jsonify(ok=True)

    @app.get('/api/session')
    def state():
        user = current()
        setup = db().execute('SELECT 1 FROM users LIMIT 1').fetchone() is None
        session.setdefault('csrf', secrets.token_hex(24))
        return jsonify(user=dict(user) if user else None, setup=setup, csrf=session['csrf'])

    @app.get('/api/health')
    def health():
        from runtime import CONFIG,VERSION
        return jsonify(app='memory-director',version=VERSION,instance=CONFIG.get('install_id',''))

    def credentials(body):
        name, password = str(body.get('name', '')).strip(), str(body.get('password', ''))
        if not 1 <= len(name) <= 30 or not 8 <= len(password) <= 128:
            abort(400, '名字需为 1–30 字，密码需为 8–128 字符')
        return name, password

    @app.post('/api/setup')
    def setup():
        if request.remote_addr not in ('127.0.0.1', '::1'):
            abort(403, '请先在家里电脑上打开 localhost 完成初始化')
        name, password = credentials(request.get_json())
        db().execute('BEGIN IMMEDIATE')
        if db().execute('SELECT 1 FROM users LIMIT 1').fetchone():
            abort(409, '已经完成初始化')
        uid = uuid.uuid4().hex
        db().execute('INSERT INTO users VALUES(?,?,?,1)', (uid, name, generate_password_hash(password)))
        db().commit()
        session.clear()
        session.update(uid=uid, csrf=secrets.token_hex(24))
        session.permanent = True
        return jsonify(ok=True)

    @app.post('/api/login')
    def login():
        body = request.get_json()
        ip = request.remote_addr
        attempt = db().execute('SELECT * FROM attempts WHERE ip=?', (ip,)).fetchone()
        if attempt and attempt['count'] >= 10 and time.time() - attempt['last'] < 300:
            abort(429, '尝试次数较多，请五分钟后再试')
        user = db().execute('SELECT * FROM users WHERE name=?', (str(body.get('name', '')),)).fetchone()
        if not user or not check_password_hash(user['password'], str(body.get('password', ''))):
            count = attempt['count'] + 1 if attempt and time.time() - attempt['last'] < 300 else 1
            db().execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (ip, count, time.time()))
            db().commit()
            abort(401, '名字或密码不正确')
        db().execute('DELETE FROM attempts WHERE ip=?', (ip,))
        db().commit()
        session.clear()
        session.update(uid=user['id'], csrf=secrets.token_hex(24))
        session.permanent = True
        return jsonify(ok=True)

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.post('/api/users')
    @login_required
    def add_user():
        if not g.user['admin']:
            abort(403)
        name, password = credentials(request.get_json())
        try:
            db().execute('INSERT INTO users VALUES(?,?,?,0)', (uuid.uuid4().hex, name, generate_password_hash(password)))
            db().commit()
        except sqlite3.IntegrityError:
            abort(409, '这个名字已有家人在使用')
        return jsonify(ok=True)

    @app.get('/api/assets')
    @login_required
    def assets():
        uid = g.user['id']
        scope = request.args.get('scope', 'mine')
        condition = {'mine': 'a.owner=?', 'shared': 'a.shared=1', 'family': 'a.shared=1', 'favorites': 'f.user IS NOT NULL',
                     'trash': 'a.owner=?',
                     'today': "substr(a.date,6,5)=strftime('%m-%d','now','localtime') AND substr(a.date,1,4)<strftime('%Y','now','localtime')"}.get(scope)
        if not condition:
            abort(400)
        where = ['(a.owner=? OR a.shared=1)', condition, 'a.purged_at IS NULL', 'a.live_parent IS NULL', 'a.deleted_at IS NOT NULL' if scope == 'trash' else 'a.deleted_at IS NULL']
        args = [uid, uid] + ([uid] if scope in ('mine','trash') else [])
        person = request.args.get('person')
        if person:
            if not db().execute('SELECT 1 FROM people WHERE id=? AND owner=?', (person,uid)).fetchone():
                abort(404)
            where.append('EXISTS(SELECT 1 FROM faces WHERE faces.asset=a.id AND faces.person=?)')
            args.append(person)
        query = request.args.get('q', '').strip()[:100]
        if query:
            where.append('(a.name LIKE ? OR a.date LIKE ? OR a.note LIKE ?)')
            args += ['%' + query + '%'] * 3
        year = request.args.get('year', '')
        if year:
            where.append('substr(a.date,1,4)=?')
            args.append(year)
        kind = request.args.get('kind', '')
        if kind:
            where.append('a.kind=?')
            args.append(kind)
        base = ' FROM assets a JOIN users u ON a.owner=u.id LEFT JOIN favorites f ON f.asset=a.id AND f.user=? WHERE ' + ' AND '.join(where)
        total = db().execute('SELECT count(*)' + base, args).fetchone()[0]
        try:
            page = max(0, int(request.args.get('page', 0)))
        except ValueError:
            abort(400)
        rows = db().execute('SELECT a.id,a.owner,a.name,a.kind,a.date,a.date_source,a.bytes,a.shared,a.source,a.deleted_at,a.note,u.name AS owner_name,(f.user IS NOT NULL) AS favorite' + base + " ORDER BY CASE WHEN a.date GLOB '[12][0-9][0-9][0-9]*' THEN 0 ELSE 1 END,a.date DESC,a.id LIMIT 60 OFFSET ?", args + [page * 60]).fetchall()
        return jsonify(items=[dict(r) for r in rows], total=total, page=page)

    @app.get('/api/stats')
    @login_required
    def stats():
        uid = g.user['id']
        counts = db().execute('SELECT count(*) total,coalesce(sum(kind=\'video\'),0) videos,coalesce(sum(shared),0) shared FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL', (uid,)).fetchone()
        years = db().execute("SELECT DISTINCT substr(date,1,4) year FROM assets WHERE (owner=? OR shared=1) AND deleted_at IS NULL AND live_parent IS NULL AND substr(date,1,4) BETWEEN '1900' AND '2100' ORDER BY year DESC", (uid,)).fetchall()
        return jsonify(**dict(counts), years=[r[0] for r in years])

    def asset_allowed(aid, owner_only=False, trash_thumb=False):
        asset = db().execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
        if not asset or asset['purged_at'] or asset['live_parent'] or (asset['owner'] != g.user['id'] and (owner_only or not asset['shared'])):
            abort(404, '找不到这份记忆')
        if asset['deleted_at'] and not (trash_thumb and asset['owner'] == g.user['id']):
            abort(404, '这份记忆已在回收站')
        return asset

    @app.get('/media/<aid>/<mode>')
    @login_required
    def media(aid, mode):
        asset = asset_allowed(aid, trash_thumb=mode == 'thumb')
        if mode not in ('thumb', 'view', 'download', 'display','play'):
            abort(404)
        if mode=='play':
            if asset['kind']!='video' or asset['playback_state']!='done' or not asset['playback_path'] or not Path(asset['playback_path']).is_file():
                abort(409,'视频仍在准备，请稍候')
            return send_file(asset['playback_path'],mimetype='video/mp4',conditional=True)
        if mode=='display':
            if asset['kind']!='image':
                abort(400)
            try:
                path,mimetype=display_image(asset,data)
            except FileNotFoundError as error:
                abort(404,str(error))
            return send_file(path,mimetype=mimetype,as_attachment=False,conditional=True)
        path = Path(asset['thumb'] if mode == 'thumb' and asset['thumb'] else asset['path'])
        if mode == 'thumb' and not asset['thumb']:
            return send_file(ROOT / 'static/film.svg')
        if not path.is_file():
            abort(404, '文件暂时不可用，请确认照片所在硬盘已连接')
        mimetype = 'video/mp4' if asset['kind'] == 'video' and Path(asset['path']).suffix.lower() not in VIDEO_EXT else None
        return send_file(path, as_attachment=mode == 'download', download_name=asset['name'], conditional=True, mimetype=mimetype)

    @app.route('/api/assets/<aid>/playback',methods=['GET','POST'])
    @login_required
    def playback(aid):
        asset=asset_allowed(aid)
        if asset['kind']!='video':abort(400,'这份素材不是视频')
        if request.method=='POST':
            db().execute('BEGIN IMMEDIATE')
            asset=db().execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
            try:signature=playback_signature(asset['path'])
            except OSError:abort(404,'原视频不在线，请连接素材硬盘')
            ready=asset['playback_state']=='done' and asset['playback_signature']==signature and asset['playback_path'] and Path(asset['playback_path']).is_file()
            busy=asset['playback_state'] in ('queued','running') and asset['playback_signature']==signature
            retry=bool((request.get_json(silent=True) or {}).get('retry'))
            if not busy and (not ready or retry):
                db().execute("UPDATE assets SET playback_state='queued',playback_signature=?,playback_error='' WHERE id=?",(signature,aid))
                enqueue(db(),asset['owner'],'playback',{'asset':aid})
            db().commit()
            asset=db().execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        return jsonify(status=asset['playback_state'],error=asset['playback_error'],url='/media/'+aid+'/play' if asset['playback_state']=='done' else None)

    @app.patch('/api/assets/<aid>')
    @login_required
    def update(aid):
        body = request.get_json()
        asset_allowed(aid, owner_only='shared' in body or 'note' in body)
        if 'note' in body:
            if not isinstance(body['note'],str) or len(body['note'])>2000:
                abort(400,'备注最多 2000 字')
            db().execute('UPDATE assets SET note=? WHERE id=?',(body['note'].strip(),aid))
        if 'shared' in body:
            db().execute('UPDATE assets SET shared=? WHERE id=?', (int(bool(body['shared'])), aid))
        if 'favorite' in body:
            if body['favorite']:
                db().execute('INSERT OR IGNORE INTO favorites VALUES(?,?)', (g.user['id'], aid))
            else:
                db().execute('DELETE FROM favorites WHERE user=? AND asset=?', (g.user['id'], aid))
        db().commit()
        return jsonify(ok=True)

    @app.post('/api/upload')
    @login_required
    def upload():
        name = request.args.get('name', '').replace('\\', '/').split('/')[-1][:240]
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXT | VIDEO_EXT:
            abort(400, '请选择照片或视频文件')
        aid = uuid.uuid4().hex
        temp = data / 'temp' / aid
        dest = data / 'uploads' / (aid + ext)
        thumb = data / 'thumbs' / (aid + '.jpg')
        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open('xb') as out:
                while chunk := request.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > app.config['MAX_CONTENT_LENGTH']:
                        abort(413, '单个文件不能超过 4 GB')
                    digest.update(chunk)
                    out.write(chunk)
            if not size:
                abort(400, '文件是空的')
            old = db().execute('SELECT id FROM assets WHERE owner=? AND hash=?', (g.user['id'], digest.hexdigest())).fetchone()
            if old:
                return jsonify(id=old['id'], duplicate=True)
            date, source = '', '日期未知'
            modified = request.args.get('modified', '')
            try:
                stamp = datetime.fromtimestamp(float(modified) / 1000)
                if 1900 <= stamp.year <= datetime.now().year + 1:
                    date, source = stamp.strftime('%Y-%m-%d %H:%M:%S'), '文件时间（待确认）'
            except (ValueError, OverflowError, OSError):
                pass
            kind = 'image' if ext in IMAGE_EXT else 'video'
            live_keys=[]
            if kind == 'image':
                try:
                    with Image.open(temp) as im:
                        exif = im.getexif()
                        raw = exif.get(36867) or exif.get_ifd(34665).get(36867)
                        if raw:
                            try:
                                date = datetime.strptime(str(raw), '%Y:%m:%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
                                source = 'EXIF 拍摄时间'
                            except ValueError:
                                pass
                        preview = ImageOps.exif_transpose(im)
                        preview.thumbnail((1200, 1200))
                        preview.convert('RGB').save(thumb, quality=85)
                    live_keys=list(image_identifiers(temp))
                except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                    abort(400, '图片无法解码，请检查文件是否完整')
            else:
                with temp.open('rb') as f:
                    head = f.read(32)
                if not (b'ftyp' in head[:16] or head.startswith((b'\x1aE\xdf\xa3', b'RIFF'))):
                    abort(400, '视频格式无法识别')
                parent=find_still(db(),{'owner':g.user['id'],'source':'upload','path':str(temp)}, {})
                if parent:
                    return jsonify(id=parent,duplicate=False,ignored_live=True)
                # Real video metadata and poster extraction; invalid videos never enter the library.
                try:
                    video_date, video_source, _ = video_metadata(temp, thumb)
                    if video_date:
                        date, source = video_date, video_source
                except Exception:
                    abort(400, '视频无法解码，请检查文件是否完整')
            temp.replace(dest)
            db().execute(f'INSERT INTO assets({ASSET_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (aid, g.user['id'], name, str(dest), str(thumb) if thumb.exists() else None,
                          kind, date, source, size, digest.hexdigest(), 0, 'upload'))
            if live_keys:
                db().execute('UPDATE assets SET live_keys=? WHERE id=?',(json.dumps(live_keys),aid))
                db().execute("UPDATE assets SET live_checked=0 WHERE owner=? AND kind='video' AND source='upload'",(g.user['id'],))
            db().commit()
            if live_keys or kind=='video':
                enqueue(db(),g.user['id'],'live')
            if (MODEL_DIR/'face_recognition_sface_2021dec.onnx').is_file():
                enqueue(db(),g.user['id'],'faces')
            return jsonify(id=aid, duplicate=False)
        except Exception:
            # A concurrent identical upload may win the unique constraint.
            db().rollback()
            dest.unlink(missing_ok=True)
            thumb.unlink(missing_ok=True)
            old = db().execute('SELECT id FROM assets WHERE owner=? AND hash=?', (g.user['id'], digest.hexdigest())).fetchone()
            if old:
                return jsonify(id=old['id'], duplicate=True)
            raise
        finally:
            temp.unlink(missing_ok=True)

    @app.post('/api/import')
    @login_required
    def import_legacy():
        if not g.user['admin']:
            abort(403)
        manifest = LEGACY / '照片总清单.csv'
        if not manifest.is_file():
            abort(404, '未配置旧格式迁移目录，请通过上传按钮选择照片或视频')
        added = missing = 0
        with manifest.open(encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                if row['status'] != '保留':
                    continue
                path = Path(row['organized']).resolve()
                if not path.is_relative_to((LEGACY / '按年月').resolve()):
                    continue
                if not path.is_file():
                    missing += 1
                    continue
                aid = 'legacy-' + row['id']
                preview = LEGACY / '预览' / (row['id'] + '.jpg')
                cursor = db().execute(f'INSERT OR IGNORE INTO assets({ASSET_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (aid, g.user['id'], Path(row['relative']).name, str(path), str(preview) if preview.is_file() else None,
                     'image', row['date'], row['date_source'], int(row['bytes'] or 0), None, 0, 'legacy'))
                added += cursor.rowcount
        db().commit()
        return jsonify(added=added, missing=missing)

    def deletion_plan(body,purge):
        try:
            rows=select_assets(db(),g.user['id'],body,purge)
            if body.get('delete_originals'):
                plan=build_plan(db(),data,LEGACY,g.user['id'],rows)
            else:
                plan={'entries':[{'id':r['id'],'name':r['name'],'files':[]} for r in rows],'count':len(rows),'links':0,'outside_links':0,
                      'signature':hashlib.sha256(json.dumps([r['id'] for r in rows]).encode()).hexdigest()}
            return rows,plan
        except PermissionError:
            abort(404,'找不到这份素材')
        except (ValueError,OSError) as error:
            abort(400,str(error))

    @app.post('/api/trash/plan')
    @login_required
    def preview_deletion():
        body=request.get_json();purge=body.get('mode')=='purge'
        rows,plan=deletion_plan(body,purge)
        token=URLSafeTimedSerializer(app.secret_key,salt='source-deletion').dumps({'owner':g.user['id'],'signature':plan['signature'],'links':plan['links'],'purge':purge,'originals':bool(body.get('delete_originals'))})
        return jsonify(count=plan['count'],links=plan['links'],outside_links=plan['outside_links'],
                       examples=[{'name':e['name'],'paths':e['files']} for e in plan['entries'][:3]],confirm=token)

    def confirm_source_deletion(body,plan,purge):
        try:
            signed=URLSafeTimedSerializer(app.secret_key,salt='source-deletion').loads(body.get('confirm',''),max_age=300)
        except BadSignature:
            abort(409,'原文件删除确认已过期，请重新预览并确认')
        if signed!={'owner':g.user['id'],'signature':plan['signature'],'links':plan['links'],'purge':purge,'originals':True}:
            abort(409,'所选素材或原文件入口已变化，请重新确认')

    @app.post('/api/trash')
    @login_required
    def trash():
        body=request.get_json();restore=bool(body.get('restore'))
        if restore and body.get('delete_originals'):
            abort(400)
        rows,plan=deletion_plan(body,bool(restore and body.get('all')))
        if body.get('delete_originals'):
            confirm_source_deletion(body,plan,False)
            return jsonify(**purge_assets(db(),data,plan,True))
        for row in rows:
            db().execute('UPDATE assets SET deleted_at=?,shared=0 WHERE id=?',(None if restore else datetime.now().isoformat(),row['id']))
        db().commit();return jsonify(ok=True)

    @app.post('/api/trash/purge')
    @login_required
    def empty_trash():
        body=request.get_json();rows,plan=deletion_plan(body,True)
        if body.get('delete_originals'):
            confirm_source_deletion(body,plan,True)
        return jsonify(**purge_assets(db(),data,plan,bool(body.get('delete_originals'))))

    @app.get('/api/storage')
    @login_required
    def storage():
        return jsonify(uploads=str(data/'uploads'),legacy=str(LEGACY),films=str(data/'films'),database=str(data/'memory.sqlite'),
                       models=str(MODEL_DIR),faces=str(data/'faces'),
                       groups=[dict(r) for r in db().execute('SELECT source,kind,count(*) count,coalesce(sum(bytes),0) bytes FROM assets WHERE owner=? GROUP BY source,kind', (g.user['id'],))])

    @app.get('/api/jobs')
    @login_required
    def jobs():
        return jsonify(items=[dict(r) for r in db().execute('SELECT id,kind,status,done,total,message FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 20', (g.user['id'],))])

    @app.post('/api/jobs/<kind>')
    @login_required
    def start_job(kind):
        if kind not in ('faces','videos'):
            abort(404)
        if kind == 'videos' and not g.user['admin']:
            abort(403)
        if kind == 'faces' and not (MODEL_DIR/'face_recognition_sface_2021dec.onnx').is_file():
            abort(409, '本地人物模型尚未安装')
        return jsonify(id=enqueue(db(),g.user['id'],kind))

    @app.patch('/api/jobs/<jid>')
    @login_required
    def change_job(jid):
        job = db().execute('SELECT * FROM jobs WHERE id=? AND owner=?',(jid,g.user['id'])).fetchone()
        if not job or job['kind'] in ('film','playback'):
            abort(404)
        status = request.get_json().get('status')
        if status not in ('paused','queued') or job['status'] not in ('running','queued','paused'):
            abort(400)
        db().execute('UPDATE jobs SET status=? WHERE id=?',(status,jid));db().commit()
        return jsonify(ok=True)

    @app.get('/api/people')
    @login_required
    def people():
        rows = db().execute('''SELECT p.id,p.name,count(DISTINCT f.asset) count,
            (SELECT f2.id FROM faces f2 JOIN assets a2 ON f2.asset=a2.id JOIN people pc ON pc.id=f2.person WHERE f2.person=p.id AND a2.deleted_at IS NULL AND a2.live_parent IS NULL ORDER BY (f2.id=pc.cover) DESC LIMIT 1) cover
            FROM people p JOIN faces f ON p.id=f.person JOIN assets a ON f.asset=a.id
            WHERE p.owner=? AND p.hidden=0 AND a.deleted_at IS NULL AND a.live_parent IS NULL GROUP BY p.id ORDER BY (p.name!='') DESC,count DESC''',(g.user['id'],)).fetchall()
        pending = db().execute("SELECT count(*) FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND face_state='pending'",(g.user['id'],)).fetchone()[0]
        errors = db().execute("SELECT count(*) FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND face_state='error'",(g.user['id'],)).fetchone()[0]
        return jsonify(items=[dict(r) for r in rows],pending=pending,errors=errors)

    @app.get('/media/face/<fid>')
    @login_required
    def face_image(fid):
        face=db().execute('SELECT f.crop FROM faces f JOIN assets a ON f.asset=a.id WHERE f.id=? AND a.owner=? AND a.deleted_at IS NULL AND a.live_parent IS NULL',(fid,g.user['id'])).fetchone()
        if not face or not Path(face['crop']).is_file():
            abort(404)
        return send_file(face['crop'],mimetype='image/jpeg')

    @app.get('/api/assets/<aid>/faces')
    @login_required
    def asset_faces(aid):
        asset_allowed(aid,owner_only=True)
        return jsonify(items=[dict(r) for r in db().execute('SELECT f.id,f.person,p.name FROM faces f JOIN people p ON f.person=p.id WHERE f.asset=?',(aid,))])

    def person_cover(pid):
        face=db().execute('''SELECT f.id FROM faces f JOIN assets a ON a.id=f.asset JOIN people p ON p.id=f.person
                            WHERE f.person=? AND a.deleted_at IS NULL AND a.purged_at IS NULL AND a.live_parent IS NULL
                            ORDER BY (f.id=p.cover) DESC LIMIT 1''',(pid,)).fetchone()
        return face['id'] if face else None

    @app.post('/api/people/merge')
    @login_required
    def people_merge():
        body=request.get_json()
        if not isinstance(body,dict):abort(400,'请选择要合并的人物分组')
        ids=body.get('ids');target=body.get('target')
        if (not isinstance(ids,list) or not 2<=len(ids)<=200 or any(not isinstance(pid,str) for pid in ids)
                or len(set(ids))!=len(ids) or not isinstance(target,str) or target not in ids):
            abort(400,'请选择 2–200 个不同分组，并从中选择一个保留的人物')
        placeholders=','.join('?' for _ in ids)
        db().execute('BEGIN IMMEDIATE')
        owned=db().execute(f'SELECT id,name FROM people WHERE owner=? AND hidden=0 AND id IN ({placeholders})',[g.user['id'],*ids]).fetchall()
        if len(owned)!=len(ids):
            abort(409,'部分人物分组已变化或不属于你的图库，请刷新后重新选择')
        cover=person_cover(target)
        sources=[pid for pid in ids if pid!=target];source_marks=','.join('?' for _ in sources)
        db().execute(f'UPDATE faces SET person=? WHERE person IN ({source_marks})',[target,*sources])
        db().execute(f'DELETE FROM people WHERE id IN ({source_marks})',sources)
        db().execute('UPDATE people SET cover=? WHERE id=?',(cover,target))
        name=next(row['name'] for row in owned if row['id']==target)
        count=db().execute('''SELECT count(DISTINCT a.id) FROM faces f JOIN assets a ON f.asset=a.id
                             WHERE f.person=? AND a.deleted_at IS NULL AND a.purged_at IS NULL AND a.live_parent IS NULL''',(target,)).fetchone()[0]
        db().commit()
        return jsonify(id=target,name=name,count=count,merged=len(sources))

    @app.patch('/api/people/<pid>')
    @login_required
    def person_edit(pid):
        person=db().execute('SELECT * FROM people WHERE id=? AND owner=?',(pid,g.user['id'])).fetchone()
        if not person:
            abort(404)
        body=request.get_json()
        if 'name' in body:
            db().execute('UPDATE people SET name=? WHERE id=?',(str(body['name']).strip()[:40],pid))
        if body.get('merge'):
            target=body['merge']
            if target==pid or not db().execute('SELECT 1 FROM people WHERE id=? AND owner=?',(target,g.user['id'])).fetchone():
                abort(400, '请选择自己图库里的另一个人物')
            cover=person_cover(target)
            db().execute('UPDATE faces SET person=? WHERE person=?',(target,pid))
            db().execute('DELETE FROM people WHERE id=?',(pid,))
            db().execute('UPDATE people SET cover=? WHERE id=?',(cover,target))
        db().commit()
        return jsonify(ok=True)

    @app.patch('/api/faces/<fid>')
    @login_required
    def face_edit(fid):
        face=db().execute('SELECT f.* FROM faces f JOIN assets a ON a.id=f.asset WHERE f.id=? AND a.owner=? AND a.deleted_at IS NULL AND a.live_parent IS NULL',(fid,g.user['id'])).fetchone()
        if not face:
            abort(404)
        body=request.get_json()
        if body.get('remove'):
            db().execute('DELETE FROM faces WHERE id=?',(fid,))
        else:
            pid=body.get('person')
            if pid == 'new':
                pid=uuid.uuid4().hex
                db().execute('INSERT INTO people(id,owner,embedding) VALUES(?,?,?)',(pid,g.user['id'],face['embedding']))
            elif not db().execute('SELECT 1 FROM people WHERE id=? AND owner=?',(pid,g.user['id'])).fetchone():
                abort(400)
            db().execute('UPDATE faces SET person=? WHERE id=?',(pid,fid))
        db().commit()
        return jsonify(ok=True)

    @app.get('/api/periods')
    @login_required
    def periods():
        return jsonify(items=[dict(r) for r in db().execute("""SELECT substr(date,1,7) period,count(*) count,min(id) cover
            FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND date GLOB '[12][0-9][0-9][0-9]-[01][0-9]*'
            GROUP BY substr(date,1,7) HAVING count(*)>=2 ORDER BY period DESC""",(g.user['id'],))])

    @app.get('/api/films')
    @login_required
    def films():
        return jsonify(items=[dict(r) for r in db().execute('''SELECT f.id,f.title,f.period,f.status,f.shared,f.notes,f.owner,
            EXISTS(SELECT 1 FROM film_assets fa JOIN assets a ON fa.asset=a.id WHERE fa.film=f.id AND a.deleted_at IS NOT NULL) blocked
            FROM films f WHERE (f.owner=? OR f.shared=1) AND f.deleted_at IS NULL ORDER BY f.created DESC''',(g.user['id'],))])

    @app.get('/api/film-options')
    @login_required
    def film_options():
        return jsonify(themes=THEMES,templates=TEMPLATES,music=[{k:r[k] for k in ('id','label','title','author','license','source')} for r in music_catalog(data,g.user['id'])])

    def validate_film_options(body):
        theme=body.get('theme','people');template=body.get('template','warm');resolution=str(body.get('resolution','1080'));music=body.get('music','random')
        if theme not in THEMES or template not in TEMPLATES or resolution not in ('720','1080') or music not in ['none','random']+[r['id'] for r in music_catalog(data,g.user['id'])]:
            abort(400,'请选择有效的主题、模板、清晰度和配乐')
        pid=body.get('person')
        if pid and not db().execute('SELECT 1 FROM people WHERE id=? AND owner=?',(pid,g.user['id'])).fetchone():
            abort(404)
        prompt=body.get('prompt','');story_mode=body.get('story_mode','event')
        if not isinstance(prompt,str) or len(prompt)>600 or story_mode not in ('event','period'):
            abort(400,'导演要求最多 600 字，请选择有效的故事范围')
        return {'theme':theme,'template':template,'resolution':resolution,'music':music,'person':pid,'prompt':prompt.strip(),'story_mode':story_mode}

    @app.post('/api/films/preview')
    @login_required
    def preview_film():
        body=request.get_json();period=str(body.get('period',''));options=validate_film_options(body)
        if not re.fullmatch(r'(19|20)\d{2}(-(0[1-9]|1[0-2]))?',period):
            abort(400,'请选择年份或月份')
        items,report=curated_assets(db(),g.user['id'],period,options['theme'],options['person'],prompt=options['prompt'],story_mode=options['story_mode'])
        return jsonify(items=items,analyzed=report['analyzed'],report=report)

    @app.get('/media/music/<mid>')
    @login_required
    def preview_music(mid):
        track=next((r for r in music_catalog(data,g.user['id']) if r['id']==mid),None)
        if not track:
            abort(404)
        return send_file(data/'music'/track['file'],conditional=True)

    @app.post('/api/music')
    @login_required
    def upload_music():
        name=request.args.get('name','').replace('\\','/').split('/')[-1][:200]
        if Path(name).suffix.lower() not in ('.mp3','.wav','.m4a','.aac','.ogg','.flac'):
            abort(400,'请选择 MP3、WAV、M4A、AAC、OGG 或 FLAC 音频')
        mid='music-'+uuid.uuid4().hex;temp=data/'temp'/mid
        folder=data/'music'/'users';folder.mkdir(parents=True,exist_ok=True)
        dest=folder/(mid+'.mp3');digest=hashlib.sha256();size=0
        try:
            with temp.open('xb') as f:
                while chunk:=request.stream.read(1024*1024):
                    size+=len(chunk)
                    if size>100*1024*1024:
                        abort(413,'单首音乐不能超过 100 MB')
                    digest.update(chunk);f.write(chunk)
            if not size:
                abort(400,'音频文件为空')
            old=db().execute('SELECT id FROM music_tracks WHERE owner=? AND hash=?',(g.user['id'],digest.hexdigest())).fetchone()
            if old:
                return jsonify(id=old[0],duplicate=True)
            try:
                ffmpeg(['-i',temp,'-map','0:a:0','-vn','-c:a','libmp3lame','-b:a','192k',dest],timeout=120)
            except Exception:
                abort(400,'无法读取这首音乐，请检查文件是否完整')
            db().execute('INSERT INTO music_tracks VALUES(?,?,?,?,?,?,?)',(mid,g.user['id'],Path(name).stem,str(dest),digest.hexdigest(),dest.stat().st_size,time.time()))
            db().commit();return jsonify(id=mid,duplicate=False)
        except Exception:
            db().rollback();dest.unlink(missing_ok=True)
            raise
        finally:
            temp.unlink(missing_ok=True)

    @app.post('/api/films')
    @login_required
    def create_film():
        body=request.get_json();period=str(body.get('period',''));options=validate_film_options(body)
        if not re.fullmatch(r'(19|20)\d{2}(-(0[1-9]|1[0-2]))?',period):
            abort(400,'请选择年份或月份')
        if db().execute('SELECT count(*) FROM assets WHERE owner=? AND deleted_at IS NULL AND live_parent IS NULL AND date LIKE ?',(g.user['id'],period+'%')).fetchone()[0]<2:
            abort(400,'这个时期至少需要两份有日期的素材')
        ids=body.get('ids')
        if ids is None:
            suggestions,_=curated_assets(db(),g.user['id'],period,options['theme'],options['person'],prompt=options['prompt'],story_mode=options['story_mode']);ids=[r['id'] for r in suggestions]
        if not isinstance(ids,list) or not 2<=len(ids)<=30 or any(not isinstance(a,str) for a in ids) or len(set(ids))!=len(ids):
            abort(400,'请预览并选择 2–30 个主题素材；不足时可调整时期或先珍藏想用的素材')
        selected=[]
        for aid in ids:
            row=db().execute('SELECT * FROM assets WHERE id=? AND owner=? AND deleted_at IS NULL AND live_parent IS NULL AND purged_at IS NULL AND date LIKE ?',(aid,g.user['id'],period+'%')).fetchone()
            if not row:
                abort(400,'选片中包含不可用或不属于该时期的素材，请重新预览')
            selected.append(row)
        _,removed=filter_selected(db(),selected)
        if removed['unavailable']:
            abort(409,'部分素材尚不可读取，请确认原片硬盘已连接、视频封面已完成处理')
        if removed['closed'] or removed['duplicates']:
            abort(400,f"选片中有 {removed['closed']} 张明显闭眼、{removed['duplicates']} 张重复照片，请重新预览选片")
        options['ids']=ids
        fid=uuid.uuid4().hex;title=str(body.get('title') or period+' · 时光来信').strip()[:60]
        db().execute('INSERT INTO films(id,owner,title,period,status,created,options) VALUES(?,?,?,?,?,?,?)',(fid,g.user['id'],title,period,'queued',time.time(),json.dumps(options)))
        db().commit();enqueue(db(),g.user['id'],'film',{'film':fid})
        return jsonify(id=fid)

    def film_allowed(fid):
        film=db().execute('SELECT * FROM films WHERE id=? AND (owner=? OR shared=1) AND deleted_at IS NULL',(fid,g.user['id'])).fetchone()
        if not film:
            abort(404)
        if db().execute('SELECT 1 FROM film_assets fa JOIN assets a ON fa.asset=a.id WHERE fa.film=? AND a.deleted_at IS NOT NULL',(fid,)).fetchone():
            abort(409,'这部影片包含回收站素材，请先恢复素材或重新制作')
        return film

    @app.get('/media/film/<fid>/<mode>')
    @login_required
    def film_media(fid,mode):
        film=film_allowed(fid)
        if mode not in ('thumb','view','download') or film['status']!='done':
            abort(404)
        path=film['thumb'] if mode=='thumb' else film['path']
        if not path or not Path(path).is_file():
            abort(404)
        return send_file(path,mimetype='image/jpeg' if mode=='thumb' else 'video/mp4',as_attachment=mode=='download',download_name=film['title']+'.mp4',conditional=True)

    @app.patch('/api/films/<fid>')
    @login_required
    def share_film(fid):
        film=film_allowed(fid)
        if film['owner']!=g.user['id']:
            abort(403)
        db().execute('UPDATE films SET shared=? WHERE id=?',(int(bool(request.get_json().get('shared'))),fid));db().commit()
        return jsonify(ok=True)

    @app.delete('/api/films/<fid>')
    @login_required
    def delete_film(fid):
        film=db().execute('SELECT * FROM films WHERE id=? AND owner=? AND deleted_at IS NULL',(fid,g.user['id'])).fetchone()
        if not film:
            abort(404)
        for value in (film['path'],film['thumb']):
            if value:
                target=Path(value).resolve()
                if not target.is_relative_to((data/'films').resolve()):
                    abort(400,'影片路径异常，未执行删除')
        db().execute('UPDATE films SET deleted_at=?,shared=0 WHERE id=?',(datetime.now().isoformat(),fid))
        for job in db().execute("SELECT id,payload FROM jobs WHERE kind='film' AND owner=? AND status IN ('queued','running')",(g.user['id'],)).fetchall():
            if json.loads(job['payload']).get('film')==fid:
                db().execute("UPDATE jobs SET status='cancelled' WHERE id=?",(job['id'],))
        db().commit()
        for value in (film['path'],film['thumb']):
            if value:
                Path(value).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.get('/api/connect')
    @login_required
    def connect():
        from transport import connection_info
        return jsonify(**connection_info(local_addresses()))

    @app.get('/api/qr')
    @login_required
    def qr():
        from transport import connection_info
        info=connection_info(local_addresses());index=request.args.get('index','0')
        mode=request.args.get('mode','native')
        if mode not in ('web','native'):abort(400)
        values=info['web_urls'] if mode=='web' else info['pairing']
        if not index.isdigit() or int(index)>=len(values):abort(400)
        content=values[int(index)] if mode=='web' else json.dumps(values[int(index)])
        out=io.BytesIO();qrcode.make(content).save(out,format='PNG');out.seek(0)
        return send_file(out,mimetype='image/png')

    return app


def local_addresses():
    result = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if any(ipaddress.ip_address(ip) in ipaddress.ip_network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
                result.add(ip)
    except OSError:
        pass
    return sorted(result)


if __name__ == '__main__':
    from server import run
    run()
