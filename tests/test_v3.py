import csv
import io
import json
import os
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image
import pytest
import app as module
from processing import connection,render_film,ffmpeg
from tests.test_app import system,write,picture


def test_original_deletion_requires_fresh_confirmation(system):
    app,a,b=system
    aid=write(a,'post','/api/upload?name=delete.jpg',data=picture()).json['id']
    conn=connection(app.config['DATA_DIR']);path=Path(conn.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone()[0]);conn.close()
    assert write(a,'post','/api/trash',json={'ids':[aid],'delete_originals':True}).status_code==409
    assert path.exists()
    plan=write(a,'post','/api/trash/plan',json={'ids':[aid],'delete_originals':True}).json
    assert plan['count']==1 and plan['links']==1
    # Another account cannot reuse this user's signed confirmation.
    assert write(b,'post','/api/trash',json={'ids':[aid],'delete_originals':True,'confirm':plan['confirm']}).status_code==404
    response=write(a,'post','/api/trash',json={'ids':[aid],'delete_originals':True,'confirm':plan['confirm']})
    assert response.json['removed']==1 and not path.exists()
    assert a.get('/api/assets?scope=trash').json['total']==0
    assert a.get('/media/'+aid+'/thumb').status_code==404
    assert write(a,'post','/api/trash',json={'ids':[aid],'restore':True}).status_code==404
    # An explicit new upload of the same bytes is allowed after deletion.
    assert write(a,'post','/api/upload?name=again.jpg',data=picture()).json['duplicate'] is False


def test_clear_all_only_my_trash_and_exclusions(system):
    app,a,b=system
    ids=[write(a,'post','/api/upload?name=a.jpg',data=picture(c)).json['id'] for c in ['red','green','blue']]
    other=write(b,'post','/api/upload?name=b.jpg',data=picture()).json['id']
    write(a,'post','/api/trash',json={'ids':ids[:2]});write(b,'post','/api/trash',json={'ids':[other]})
    response=write(a,'post','/api/trash/purge',json={'all':True,'excluded':[ids[0]]})
    assert response.json['removed']==1
    assert a.get('/api/assets?scope=trash').json['total']==1
    assert a.get('/api/assets').json['total']==1
    assert b.get('/api/assets?scope=trash').json['total']==1
    # Default clearing removes records only, keeping disk originals.
    conn=connection(app.config['DATA_DIR']);assert Path(conn.execute('SELECT path FROM assets WHERE id=?',(ids[1],)).fetchone()[0]).exists();conn.close()
    response=write(a,'post','/api/trash/purge',json={'all':True})
    assert response.json['removed']==1
    assert a.get('/api/assets?scope=trash').json['total']==0


def test_legacy_hardlinks_are_scoped_and_import_not_resurrected(system,tmp_path,monkeypatch):
    app,a,b=system
    legacy=tmp_path/'备份'/'_照片整理';organized=legacy/'按年月';organized.mkdir(parents=True)
    raw=legacy.parent/'2026年照片备份';raw.mkdir()
    original=raw/'one.jpg';original.write_bytes(picture())
    linked=organized/'one.jpg';os.link(original,linked)
    (legacy/'照片总清单.csv').write_text('id,status,organized,relative,date,date_source,bytes\none,保留,'+str(linked)+',2026年照片备份/one.jpg,2026-07-01,EXIF,'+str(len(picture()))+'\n',encoding='utf-8-sig')
    monkeypatch.setattr(module,'LEGACY',legacy)
    assert write(a,'post','/api/import').json['added']==1
    aid='legacy-one';plan=write(a,'post','/api/trash/plan',json={'ids':[aid],'delete_originals':True}).json
    assert plan['links']==2,plan
    response=write(a,'post','/api/trash',json={'ids':[aid],'delete_originals':True,'confirm':plan['confirm']})
    assert response.json['removed']==1
    assert not original.exists() and not linked.exists()
    assert write(a,'post','/api/import').json['added']==0


def test_source_deletion_rejects_foreign_paths_and_stale_all_selection(system,tmp_path):
    app,a,b=system
    aid=write(a,'post','/api/upload?name=a.jpg',data=picture()).json['id']
    second=write(a,'post','/api/upload?name=b.jpg',data=picture('blue')).json['id']
    write(a,'post','/api/trash',json={'ids':[aid]})
    plan=write(a,'post','/api/trash/plan',json={'all':True,'mode':'purge','delete_originals':True}).json
    write(a,'post','/api/trash',json={'ids':[second]})
    assert write(a,'post','/api/trash/purge',json={'all':True,'delete_originals':True,'confirm':plan['confirm']}).status_code==409
    outside=tmp_path/'outside.jpg';outside.write_bytes(picture())
    conn=connection(app.config['DATA_DIR']);conn.execute('UPDATE assets SET path=? WHERE id=?',(str(outside),aid));conn.commit();conn.close()
    assert write(a,'post','/api/trash/plan',json={'ids':[aid],'delete_originals':True}).status_code==400
    assert outside.exists()


def test_render_original_1080_and_real_music_then_delete_film(system):
    app,a,b=system
    grid=np.indices((1080,1920)).sum(axis=0)//12%2*255
    image=np.stack([grid,grid,grid],axis=-1).astype(np.uint8)
    ids=[]
    for i in range(2):
        image[0:100,0:100]=[i*255,80,90]
        stream=io.BytesIO();Image.fromarray(image).save(stream,format='PNG')
        ids.append(write(a,'post','/api/upload?name=original.png&modified=1782864000000',data=stream.getvalue()).json['id'])
    conn=connection(app.config['DATA_DIR'])
    for aid in ids:
        thumb=conn.execute('SELECT thumb FROM assets WHERE id=?',(aid,)).fetchone()[0]
        Image.new('RGB',(16,16),'gray').save(thumb)
    music=app.config['DATA_DIR']/'music';music.mkdir()
    from runtime import DATA_DIR
    src=DATA_DIR/'music/forget-me-not.ogg'
    if not src.exists():
        pytest.skip('CC0 test soundtrack not installed')
    shutil.copy2(src,music/src.name)
    response=write(a,'post','/api/films',json={'period':'2026-07','ids':ids,'music':'forget-me-not','resolution':'1080','theme':'favorites','template':'postcard'})
    assert response.status_code==200
    fid=response.json['id'];job=conn.execute("SELECT * FROM jobs WHERE kind='film'").fetchone();render_film(conn,app.config['DATA_DIR'],job)
    film=conn.execute('SELECT * FROM films WHERE id=?',(fid,)).fetchone();path=Path(film['path'])
    executable=__import__('imageio_ffmpeg').get_ffmpeg_exe()
    frame=subprocess.run([executable,'-v','error','-ss','3','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','gray','-'],capture_output=True).stdout
    pixels=np.frombuffer(frame,np.uint8).reshape((1080,1920))
    assert pixels[250:750,500:1400].std()>60  # A flat 16px preview would fail this check.
    audio=subprocess.run([executable,'-v','error','-i',str(path),'-t','3','-f','f32le','-ac','1','-'],capture_output=True).stdout
    assert np.abs(np.frombuffer(audio,np.float32)).mean()>.001
    assert '1920×1080' in film['notes'] and 'CC0' in film['notes']
    assert write(b,'delete','/api/films/'+fid).status_code==404
    assert write(a,'delete','/api/films/'+fid).status_code==200
    assert not path.exists() and not Path(film['thumb']).exists()
    assert a.get('/api/films').json['items']==[]
    assert a.get('/api/assets').json['total']==2
    assert all(Path(r[0]).exists() for r in conn.execute('SELECT path FROM assets'))
    conn.close()


def test_theme_selection_rejects_screenshot_and_has_no_random_fallback(system,monkeypatch):
    import curation
    app,a,b=system
    ids=[write(a,'post','/api/upload?name=sample.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','green','blue']]
    def analyze(conn,row):
        scores={k:.1 for k in curation.PROMPTS}
        scores['screenshot' if row['id']==ids[0] else 'nature']=.7
        return {'scores':scores,'width':1920,'height':1080,'blur':100,'brightness':120,'contrast':50,'hash':str(0 if row['id']==ids[1] else 2**64-1)}
    monkeypatch.setattr(curation,'analyze_asset',analyze)
    conn=connection(app.config['DATA_DIR']);items,_=curation.curated_assets(conn,a.get('/api/session').json['user']['id'],'2026-07','nature');conn.close()
    assert {r['id'] for r in items}==set(ids[1:])
    response=write(a,'post','/api/films/preview',json={'period':'2026-07','theme':'people'})
    assert response.json['items']==[]
    assert write(a,'post','/api/films',json={'period':'2026-07','theme':'people'}).status_code==400


def test_delete_queued_film_cancels_without_touching_sources(system):
    app,a,b=system
    ids=[write(a,'post','/api/upload?name=sample.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','blue']]
    fid=write(a,'post','/api/films',json={'period':'2026-07','ids':ids,'music':'none'}).json['id']
    assert write(a,'delete','/api/films/'+fid).status_code==200
    conn=connection(app.config['DATA_DIR'])
    assert conn.execute("SELECT status FROM jobs WHERE kind='film'").fetchone()[0]=='cancelled'
    assert a.get('/api/assets').json['total']==2
    assert all(Path(r[0]).exists() for r in conn.execute('SELECT path FROM assets'))
    conn.close()


def test_all_restore_and_missing_source_are_explicit(system):
    app,a,b=system
    aid=write(a,'post','/api/upload?name=sample.jpg',data=picture()).json['id']
    write(a,'post','/api/trash',json={'ids':[aid]})
    assert write(a,'post','/api/trash',json={'all':True,'restore':True}).status_code==200
    assert a.get('/api/assets').json['total']==1
    conn=connection(app.config['DATA_DIR']);path=Path(conn.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone()[0]);conn.close()
    path.unlink()
    response=write(a,'post','/api/trash/plan',json={'ids':[aid],'delete_originals':True})
    assert response.status_code==400 and '原片当前不可用' in response.json['error']
