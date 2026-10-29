import csv
import io
import sqlite3
from pathlib import Path

from PIL import Image
import pytest
import app as module


@pytest.fixture
def system(tmp_path):
    app = module.create_app(tmp_path / 'data')
    app.config['TESTING'] = True
    a, b = app.test_client(), app.test_client()
    assert a.post('/api/setup', json={'name':'导演','password':'director-test-password'}).status_code == 200
    csrf = a.get('/api/session').json['csrf']
    assert a.post('/api/users', json={'name':'家人','password':'family-test-password'}, headers={'X-CSRF-Token':csrf}).status_code == 200
    assert b.post('/api/login', json={'name':'家人','password':'family-test-password'}).status_code == 200
    return app, a, b


def write(client, method, url, **kwargs):
    token = client.get('/api/session').json['csrf']
    return getattr(client, method)(url, headers={'X-CSRF-Token':token}, **kwargs)


def picture(color='green'):
    buffer = io.BytesIO()
    Image.new('RGB', (120, 80), color).save(buffer, format='JPEG')
    return buffer.getvalue()


def test_private_share_revoke_download(system):
    app,a,b = system
    result = write(a,'post','/api/upload?name=回忆.jpg',data=picture())
    assert result.status_code == 200
    aid = result.json['id']
    assert a.get('/api/assets').json['total'] == 1
    assert b.get('/api/assets').json['total'] == 0
    assert b.get('/api/assets?scope=family').json['total'] == 0
    for mode in ('thumb','view','download'):
        assert b.get(f'/media/{aid}/{mode}').status_code == 404
    assert write(b,'patch',f'/api/assets/{aid}',json={'shared':True}).status_code == 404
    assert write(a,'patch',f'/api/assets/{aid}',json={'shared':True}).status_code == 200
    assert b.get('/api/assets?scope=family').json['total'] == 1
    assert b.get(f'/media/{aid}/download').data == picture()
    assert write(b,'patch',f'/api/assets/{aid}',json={'favorite':True}).status_code == 200
    assert b.get('/api/assets?scope=favorites').json['total'] == 1
    assert write(a,'patch',f'/api/assets/{aid}',json={'shared':False}).status_code == 200
    assert b.get('/api/assets?scope=favorites').json['total'] == 0
    assert b.get(f'/media/{aid}/download').status_code == 404


def test_upload_duplicate_per_owner_and_cleanup(system):
    app,a,b=system
    first=write(a,'post','/api/upload?name=test.jpg',data=picture()).json
    duplicate=write(a,'post','/api/upload?name=another.jpg',data=picture()).json
    assert duplicate == {'id':first['id'],'duplicate':True}
    assert write(b,'post','/api/upload?name=test.jpg',data=picture()).json['duplicate'] is False
    for name,body in [('bad.jpg',b'invalid image'),('bad.mov',b'not video'),('empty.jpg',b''),('script.html',b'<script>')]:
        assert write(a,'post','/api/upload?name='+name,data=body).status_code == 400
    assert list((app.config['DATA_DIR']/'temp').iterdir()) == []
    assert len(list((app.config['DATA_DIR']/'uploads').iterdir())) == 2
    assert len(list((app.config['DATA_DIR']/'thumbs').iterdir())) == 2


def test_auth_csrf_origin_setup(system):
    app,a,b=system
    anon=app.test_client()
    assert anon.get('/api/assets').status_code == 401
    assert anon.get('/media/no/download').status_code == 401
    assert anon.post('/api/setup',json={'name':'bad','password':'long-password'}).status_code == 409
    assert a.post('/api/upload?name=a.jpg',data=picture()).status_code == 403
    assert a.post('/api/login',json={},headers={'Origin':'http://evil.example'}).status_code == 403
    assert a.get('/api/session',base_url='http://evil.example').status_code == 403
    assert a.get('/data/memory.sqlite').status_code == 404
    assert write(b,'post','/api/users',json={'name':'other','password':'long-password'}).status_code == 403
    assert write(b,'post','/api/import').status_code == 403
    assert a.get('/api/assets?page=bad').status_code == 400
    assert a.get('/api/assets?scope=invalid').status_code == 400


def test_legacy_readonly_idempotent(system,tmp_path,monkeypatch):
    app,a,b=system
    legacy=tmp_path/'legacy';(legacy/'按年月').mkdir(parents=True);(legacy/'预览').mkdir()
    path=legacy/'按年月'/'test.jpg';path.write_bytes(picture())
    before=path.stat().st_mtime_ns
    fields=['id','status','organized','relative','date','date_source','bytes']
    with (legacy/'照片总清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for id,status,p in [('one','保留',path),('two','已隔离',path),('three','保留',tmp_path/'outside.jpg')]:
            writer.writerow(dict(id=id,status=status,organized=str(p),relative='test.jpg',date='2020-01-01',date_source='EXIF',bytes=len(picture())))
    monkeypatch.setattr(module,'LEGACY',legacy)
    assert write(a,'post','/api/import').json['added']==1
    assert write(a,'post','/api/import').json['added']==0
    assert b.get('/api/assets?scope=family').json['total']==0
    assert a.get('/media/legacy-one/download').data==picture()
    assert path.stat().st_mtime_ns==before


def test_video_range_and_upload_limit(system):
    app,a,b=system
    from processing import ffmpeg
    sample=app.config['DATA_DIR']/'sample.mp4'
    ffmpeg(['-f','lavfi','-i','color=c=green:s=320x240:r=24','-t','0.5','-c:v','libx264','-pix_fmt','yuv420p',sample])
    video=sample.read_bytes()
    aid=write(a,'post','/api/upload?name=video.mp4',data=video).json['id']
    response=a.get(f'/media/{aid}/view',headers={'Range':'bytes=0-9'})
    assert response.status_code==206 and response.data==video[:10]
    app.config['MAX_CONTENT_LENGTH']=32
    assert write(a,'post','/api/upload?name=big.jpg',data=picture()).status_code==413
    assert not list((app.config['DATA_DIR']/'temp').iterdir())


def test_trash_restore_permissions_and_filters(system):
    app,a,b=system
    aid=write(a,'post','/api/upload?name=photo.jpg',data=picture()).json['id']
    write(a,'patch',f'/api/assets/{aid}',json={'shared':True,'favorite':True})
    assert write(b,'post','/api/trash',json={'ids':[aid]}).status_code==404
    assert write(a,'post','/api/trash',json={'ids':[aid]}).status_code==200
    assert a.get('/api/assets').json['total']==0
    assert a.get('/api/assets?scope=favorites').json['total']==0
    assert b.get('/api/assets?scope=shared').json['total']==0
    assert a.get('/api/assets?scope=trash').json['total']==1
    assert a.get('/api/stats').json['total']==0
    assert a.get(f'/media/{aid}/thumb').status_code==200
    assert a.get(f'/media/{aid}/download').status_code==404
    assert b.get(f'/media/{aid}/thumb').status_code==404
    assert write(a,'post','/api/trash',json={'ids':[aid],'restore':True}).status_code==200
    assert a.get('/api/assets').json['total']==1
    assert a.get('/api/assets?scope=favorites').json['total']==1
    assert b.get('/api/assets?scope=shared').json['total']==0
    assert a.get(f'/media/{aid}/download').data==picture()


def test_people_grouping_and_owner_acl(system):
    import numpy as np
    from processing import connection,group_faces
    app,a,b=system
    aid=write(a,'post','/api/upload?name=person.jpg',data=picture()).json['id']
    owner=a.get('/api/session').json['user']['id']
    conn=connection(app.config['DATA_DIR']);vec=np.zeros(128,np.float32);vec[0]=1
    group_faces(conn,owner,aid,[(Image.new('RGB',(50,50),'red'),vec)],app.config['DATA_DIR'])
    group_faces(conn,owner,aid,[(Image.new('RGB',(50,50),'red'),vec)],app.config['DATA_DIR']);conn.commit();conn.close()
    people=a.get('/api/people').json['items'];assert len(people)==1 and people[0]['count']==1
    pid=people[0]['id'];fid=people[0]['cover']
    assert b.get('/api/people').json['items']==[]
    assert b.get('/media/face/'+fid).status_code==404
    assert b.get('/api/assets?person='+pid).status_code==404
    assert write(b,'patch','/api/people/'+pid,json={'name':'wrong'}).status_code==404
    assert write(a,'patch','/api/people/'+pid,json={'name':'家人'}).status_code==200
    assert a.get('/api/assets?person='+pid).json['total']==1
    assert write(a,'patch','/api/faces/'+fid,json={'person':'new'}).status_code==200
    groups=a.get('/api/people').json['items'];assert len(groups)==2
    target=next(p['id'] for p in groups if p['id']!=pid)
    assert write(a,'patch','/api/people/'+target,json={'merge':pid}).status_code==200
    assert len(a.get('/api/people').json['items'])==1


def test_film_render_and_revocation(system):
    import json
    from processing import connection,render_film
    app,a,b=system
    ids=[write(a,'post','/api/upload?name='+name+'&modified=1782864000000',data=picture(color)).json['id'] for name,color in [('one.jpg','red'),('two.jpg','blue')]]
    response=write(a,'post','/api/films',json={'period':'2026-07','title':'夏日回忆','ids':ids,'music':'none','resolution':'720','template':'postcard'})
    assert response.status_code==200
    fid=response.json['id'];conn=connection(app.config['DATA_DIR']);job=conn.execute("SELECT * FROM jobs WHERE kind='film'").fetchone()
    render_film(conn,app.config['DATA_DIR'],job);conn.close()
    assert a.get('/media/film/'+fid+'/view').status_code==200
    assert b.get('/media/film/'+fid+'/view').status_code==404
    assert write(a,'patch','/api/films/'+fid,json={'shared':True}).status_code==200
    assert b.get('/media/film/'+fid+'/view',headers={'Range':'bytes=0-9'}).status_code==206
    write(a,'post','/api/trash',json={'ids':[ids[0]]})
    assert b.get('/media/film/'+fid+'/view').status_code==409
    assert a.get('/api/films').json['items'][0]['blocked']==1
    write(a,'post','/api/trash',json={'ids':[ids[0]],'restore':True})
    assert b.get('/media/film/'+fid+'/view').status_code==200


def test_video_import_handles_mismatch_and_excludes_backups(system,tmp_path,monkeypatch):
    import json
    from processing import connection,enqueue,import_videos,video_posters,ffmpeg
    app,a,b=system
    root=tmp_path/'backup';legacy=root/'_legacy-gallery';legacy.mkdir(parents=True)
    folder=root/'2026年照片备份'/'202607_a';folder.mkdir(parents=True)
    real=folder/'real.mp4';ffmpeg(['-f','lavfi','-i','color=c=blue:s=320x240:r=24','-t','0.4','-c:v','libx264','-pix_fmt','yuv420p',real])
    disguised=folder/'hidden.jpg';disguised.write_bytes(real.read_bytes())
    false=folder/'actually-image.mp4';false.write_bytes(picture())
    with (legacy/'扩展名错配清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['relative','kind']);w.writeheader();w.writerows([{'relative':str(p.relative_to(root)),'kind':k} for p,k in [(disguised,'video'),(false,'image')]])
    owner=a.get('/api/session').json['user']['id'];conn=connection(app.config['DATA_DIR']);jid=enqueue(conn,owner,'videos');job=conn.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
    assert import_videos(conn,app.config['DATA_DIR'],job,legacy)
    assert conn.execute("SELECT count(*) FROM assets WHERE kind='video'").fetchone()[0]==2
    assert import_videos(conn,app.config['DATA_DIR'],job,legacy)
    assert conn.execute("SELECT count(*) FROM assets WHERE kind='video'").fetchone()[0]==2
    posterjob=conn.execute("SELECT * FROM jobs WHERE kind='posters'").fetchone()
    assert video_posters(conn,app.config['DATA_DIR'],posterjob)
    assert conn.execute("SELECT count(*) FROM assets WHERE kind='video' AND thumb IS NOT NULL").fetchone()[0]==2
    conn.close()


def test_login_throttling(system):
    app,a,b=system
    c=app.test_client()
    for i in range(10):
        assert c.post('/api/login',json={'name':'导演','password':'wrong'}).status_code==401
    assert c.post('/api/login',json={'name':'导演','password':'director-test-password'}).status_code==429
