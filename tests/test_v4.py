import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from pillow_heif import from_pillow

from tests.test_app import system,write,picture
from processing import connection,ffmpeg,enqueue,render_film
from media_support import image_identifiers,movie_identifier,scan_live_photos

PAIR='A8B0B7F4-DA81-4AC9-948F-F7B5CD1AC233'


def live_image():
    exif=Image.Exif();exif[34665]={37500:b'Apple iOS\0'+PAIR.encode()}
    out=io.BytesIO();Image.new('RGB',(1600,1200),'green').save(out,format='JPEG',exif=exif)
    return out.getvalue()


def movie(path,live=False):
    ffmpeg(['-f','lavfi','-i','color=c=blue:s=320x240:r=24','-t','0.5','-c:v','libx264','-pix_fmt','yuv420p',
            *(['-metadata','com.apple.quicktime.content.identifier='+PAIR,'-movflags','use_metadata_tags'] if live else []),path])
    return path.read_bytes()


def test_full_size_view_without_download_and_acl(system):
    app,a,b=system
    buf=io.BytesIO();Image.new('RGB',(2400,1800),'red').save(buf,format='PNG')
    aid=write(a,'post','/api/upload?name=detail.png',data=buf.getvalue()).json['id']
    conn=connection(app.config['DATA_DIR']);thumb=conn.execute('SELECT thumb FROM assets WHERE id=?',(aid,)).fetchone()[0];Image.new('RGB',(16,16),'gray').save(thumb);conn.close()
    response=a.get('/media/'+aid+'/display')
    assert response.status_code==200 and response.data==buf.getvalue()
    assert 'attachment' not in response.headers.get('Content-Disposition','')
    assert Image.open(io.BytesIO(response.data)).size==(2400,1800)
    assert b.get('/media/'+aid+'/display').status_code==404


def test_heic_full_resolution_reusable_cache(system):
    app,a,b=system
    buf=io.BytesIO();from_pillow(Image.new('RGB',(1800,2400),'#7788aa')).save(buf,quality=90)
    aid=write(a,'post','/api/upload?name=portrait.heic',data=buf.getvalue()).json['id']
    result=a.get('/media/'+aid+'/display')
    assert result.status_code==200 and result.mimetype=='image/jpeg'
    assert Image.open(io.BytesIO(result.data)).size==(1800,2400)
    again=a.get('/media/'+aid+'/display');assert again.data==result.data
    assert len(list((app.config['DATA_DIR']/'display').glob('*.jpg')))==1
    assert a.get('/media/'+aid+'/download').data==buf.getvalue()


def test_uploaded_live_pair_ignored_regular_short_movie_kept(system):
    app,a,b=system
    still=write(a,'post','/api/upload?name=IMG_0920.JPG',data=live_image()).json['id']
    path=app.config['DATA_DIR']/'live.mov';data=movie(path,True)
    assert movie_identifier(path)==PAIR
    result=write(a,'post','/api/upload?name=IMG_0920.MOV',data=data)
    assert result.status_code==200 and result.json['ignored_live'] and result.json['id']==still
    assert a.get('/api/stats').json['videos']==0
    regular=movie(app.config['DATA_DIR']/'regular.mov')
    assert write(a,'post','/api/upload?name=IMG_0920.MOV',data=regular).status_code==200
    assert a.get('/api/stats').json['videos']==1
    assert not list((app.config['DATA_DIR']/'temp').iterdir())


def test_existing_live_pair_hidden_without_deleting_files(system,tmp_path):
    app,a,b=system
    folder=tmp_path/'old';folder.mkdir();still=folder/'renamed-photo.jpg';still.write_bytes(live_image())
    clip=folder/'IMG_0920.mov';movie(clip,True)
    ordinary=folder/'normal.mov';movie(ordinary)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [still,clip,ordinary]}
    owner=a.get('/api/session').json['user']['id'];conn=connection(app.config['DATA_DIR'])
    for aid,path,kind in [('still',still,'image'),('live',clip,'video'),('normal',ordinary,'video')]:
        conn.execute('INSERT INTO assets(id,owner,name,path,kind,date,date_source,bytes,source) VALUES(?,?,?,?,?,?,?,?,?)',(aid,owner,path.name,str(path),kind,'2026-07-01','EXIF',path.stat().st_size,'legacy'))
    conn.commit();jid=enqueue(conn,owner,'live');job=conn.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone();assert scan_live_photos(conn,app.config['DATA_DIR'],job,folder/'_照片整理')
    assert conn.execute('SELECT live_parent FROM assets WHERE id=?',('live',)).fetchone()[0]=='still'
    assert a.get('/api/stats').json['videos']==1
    assert a.get('/api/assets').json['total']==2
    assert a.get('/media/live/view').status_code==404
    assert a.get('/media/normal/view').status_code==200
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [still,clip,ordinary]}==before
    conn.close()


def test_notes_persist_search_and_owner_control(system):
    app,a,b=system
    aid=write(a,'post','/api/upload?name=photo.jpg',data=picture()).json['id']
    note='海边散步\n<script>这个字符串只是备注</script>'
    assert write(a,'patch','/api/assets/'+aid,json={'note':note}).status_code==200
    assert a.get('/api/assets?q=海边散步').json['items'][0]['note']==note
    write(a,'patch','/api/assets/'+aid,json={'shared':True})
    assert write(b,'patch','/api/assets/'+aid,json={'note':'changed'}).status_code==404
    assert write(a,'patch','/api/assets/'+aid,json={'note':'x'*2001}).status_code==400


def test_live_movie_without_photo_recovers_still_then_prefers_original(system):
    app,a,b=system;data=app.config['DATA_DIR']
    owner=a.get('/api/session').json['user']['id']
    clip=movie(data/'orphan.mov',True)
    vid=write(a,'post','/api/upload?name=orphan.MOV',data=clip).json['id']
    conn=connection(data);jid=enqueue(conn,owner,'live');job=conn.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    assert scan_live_photos(conn,data,job,data/'missing-legacy')
    items=a.get('/api/assets').json['items'];assert len(items)==1 and items[0]['kind']=='image'
    recovered=items[0]['id'];assert Image.open(io.BytesIO(a.get('/media/'+recovered+'/display').data)).size==(320,240)
    assert a.get('/api/stats').json['videos']==0
    assert conn.execute('SELECT path FROM assets WHERE id=?',(vid,)).fetchone() and a.get('/media/'+vid+'/view').status_code==404
    original=write(a,'post','/api/upload?name=original.JPG',data=live_image()).json['id']
    assert scan_live_photos(conn,data,job,data/'missing-legacy')
    assert [i['id'] for i in a.get('/api/assets').json['items']]==[original]
    assert conn.execute('SELECT live_parent FROM assets WHERE id=?',(vid,)).fetchone()[0]==original
    assert conn.execute('SELECT live_parent FROM assets WHERE id=?',(recovered,)).fetchone()[0]==original
    conn.close()


def test_custom_music_owner_scope_duplicate_and_film(system):
    app,a,b=system
    source=app.config['DATA_DIR']/'tune.wav'
    ffmpeg(['-f','lavfi','-i','sine=frequency=350:sample_rate=48000','-t','1',source])
    response=write(a,'post','/api/music?name=我的配乐.wav',data=source.read_bytes());assert response.status_code==200
    mid=response.json['id'];assert a.get('/media/music/'+mid).status_code==200
    assert b.get('/media/music/'+mid).status_code==404
    tracks=a.get('/api/film-options').json['music'];assert any(t['id']==mid and t['license']=='用户提供' for t in tracks)
    assert all(t['id']!=mid for t in b.get('/api/film-options').json['music'])
    assert write(a,'post','/api/music?name=again.wav',data=source.read_bytes()).json=={'id':mid,'duplicate':True}
    assert write(a,'post','/api/music?name=bad.mp3',data=b'not audio').status_code==400
    ids=[write(a,'post','/api/upload?name=a.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','blue']]
    fid=write(a,'post','/api/films',json={'period':'2026-07','ids':ids,'music':mid,'resolution':'720','template':'postcard'}).json['id']
    conn=connection(app.config['DATA_DIR']);job=conn.execute("SELECT * FROM jobs WHERE kind='film'").fetchone();render_film(conn,app.config['DATA_DIR'],job)
    notes=conn.execute('SELECT notes FROM films WHERE id=?',(fid,)).fetchone()[0]
    assert '我的配乐' in notes and '本地上传' in notes and 'CC0' not in notes
    conn.close();assert not list((app.config['DATA_DIR']/'temp').iterdir())
