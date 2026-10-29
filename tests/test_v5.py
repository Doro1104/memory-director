import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest

import curation
from film_rules import split_brief,text_vector
from processing import connection,ffmpeg,prepare_playback
from tests.test_app import system,write,picture


def hevc(path):
    ffmpeg(['-f','lavfi','-i','testsrc2=s=320x240:r=24','-f','lavfi','-i','sine=frequency=400',
            '-t','0.6','-c:v','libx265','-preset','ultrafast','-x265-params','pools=1:frame-threads=1:log-level=error',
            '-pix_fmt','yuv420p','-c:a','aac',path])
    return path.read_bytes()


def test_hevc_playback_h264_ranges_reuse_acl_and_original(system):
    app,a,b=system;data=app.config['DATA_DIR'];original=hevc(data/'hevc.mp4')
    aid=write(a,'post','/api/upload?name=hevc.mp4',data=original).json['id']
    assert write(b,'post','/api/assets/'+aid+'/playback').status_code==404
    assert write(a,'post','/api/assets/'+aid+'/playback').json['status']=='queued'
    assert write(a,'post','/api/assets/'+aid+'/playback').json['status']=='queued'
    assert a.get('/media/'+aid+'/play').status_code==409
    conn=connection(data);assert conn.execute("SELECT count(*) FROM jobs WHERE kind='playback'").fetchone()[0]==1
    job=conn.execute("SELECT * FROM jobs WHERE kind='playback'").fetchone();prepare_playback(conn,data,job)
    ready=a.get('/api/assets/'+aid+'/playback').json;assert ready['status']=='done'
    row=conn.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone();target=Path(row['playback_path']);stamp=target.stat().st_mtime_ns
    info=subprocess.run(['ffprobe','-v','error','-show_streams','-of','json',str(target)],capture_output=True,check=True)
    streams=json.loads(info.stdout)['streams'];video=next(s for s in streams if s['codec_type']=='video')
    assert next(s for s in streams if s['codec_type']=='audio')['codec_name']=='aac'
    assert (video['codec_name'],video['pix_fmt'],video['width'],video['height'])==('h264','yuv420p',320,240)
    response=a.get(ready['url'],headers={'Range':'bytes=0-63'});assert response.status_code==206 and len(response.data)==64
    assert response.mimetype=='video/mp4' and b.get(ready['url']).status_code==404
    response.close()
    assert a.get('/media/'+aid+'/download').data==original
    assert hashlib.sha256(Path(row['path']).read_bytes()).digest()==hashlib.sha256(original).digest()
    assert write(a,'post','/api/assets/'+aid+'/playback').json['status']=='done'
    assert target.stat().st_mtime_ns==stamp and conn.execute("SELECT count(*) FROM jobs WHERE kind='playback'").fetchone()[0]==1
    write(a,'patch','/api/assets/'+aid,json={'shared':True})
    with b.get(ready['url']) as shared:assert shared.status_code==200
    write(a,'patch','/api/assets/'+aid,json={'shared':False});assert b.get(ready['url']).status_code==404
    Path(row['path']).touch()
    assert write(a,'post','/api/assets/'+aid+'/playback').json['status']=='queued'
    write(a,'post','/api/trash',json={'ids':[aid]})
    assert write(a,'post','/api/trash/purge',json={'ids':[aid]}).status_code==200
    assert not target.exists() and Path(row['path']).is_file()
    conn.close()


def test_failed_playback_reports_error_and_can_retry(system):
    app,a,b=system;data=app.config['DATA_DIR'];original=hevc(data/'source.mp4')
    aid=write(a,'post','/api/upload?name=source.mp4',data=original).json['id']
    conn=connection(data);source=Path(conn.execute('SELECT path FROM assets WHERE id=?',(aid,)).fetchone()[0]);source.write_bytes(b'broken-video')
    write(a,'post','/api/assets/'+aid+'/playback');job=conn.execute("SELECT * FROM jobs WHERE kind='playback'").fetchone()
    with pytest.raises(ValueError):prepare_playback(conn,data,job)
    assert a.get('/api/assets/'+aid+'/playback').json['status']=='error'
    assert not list((data/'playback').glob('*.part.mp4')) and source.read_bytes()==b'broken-video'
    assert write(a,'post','/api/assets/'+aid+'/playback',json={'retry':True}).json['status']=='queued'
    conn.close()


def values(number,closed=False,blur=100):
    scores={k:.1 for k in curation.PROMPTS};scores['nature']=.7
    return {'scores':scores,'width':1920,'height':1080,'blur':blur,'brightness':120,'contrast':50,
            'hash':str(number),'phash':str(number),'color':[100,100,100],
            'eyes':{'checked':1,'closed':int(closed),'openness':.95 if not closed else .05}}


def test_closed_and_near_duplicates_excluded_even_from_favorites(system,monkeypatch):
    app,a,b=system;ids=[write(a,'post','/api/upload?name=a.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','green','blue','orange']]
    for aid in ids:write(a,'patch','/api/assets/'+aid,json={'favorite':True})
    samples={ids[0]:values(0,closed=True),ids[1]:values(8,blur=80),ids[2]:values(9,blur=300),ids[3]:values(2**64-1)}
    monkeypatch.setattr(curation,'analyze_asset',lambda conn,row:samples[row['id']])
    preview=write(a,'post','/api/films/preview',json={'period':'2026-07','theme':'favorites'}).json
    assert {r['id'] for r in preview['items']}=={ids[2],ids[3]}
    assert preview['report']['closed']==1 and preview['report']['duplicates']==1
    assert write(a,'post','/api/films',json={'period':'2026-07','theme':'favorites','ids':[ids[0],ids[3]],'music':'none'}).status_code==400
    assert write(a,'post','/api/films',json={'period':'2026-07','theme':'favorites','ids':[ids[1],ids[2]],'music':'none'}).status_code==400
    assert write(a,'post','/api/films',json={'period':'2026-07','theme':'favorites','ids':[ids[2],ids[3]],'music':'none'}).status_code==200


def test_event_grouping_crosses_media_and_does_not_mix_different_days(system,monkeypatch):
    app,a,b=system;ids=[write(a,'post','/api/upload?name=a.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','blue','green','orange']]
    conn=connection(app.config['DATA_DIR'])
    for aid,date in zip(ids,['2026-07-01 09:00:00','2026-07-01 10:00:00','2026-07-20 09:00:00','2026-07-20 10:00:00']):conn.execute('UPDATE assets SET date=? WHERE id=?',(date,aid))
    conn.execute("UPDATE assets SET kind='video' WHERE id=?",(ids[1],));conn.commit();conn.close()
    samples={aid:values(number) for aid,number in zip(ids,[0,0xFFFFFFFFFFFFFFFF,0xAAAAAAAAAAAAAAAA,0x5555555555555555])}
    monkeypatch.setattr(curation,'analyze_asset',lambda conn,row:samples[row['id']])
    event=write(a,'post','/api/films/preview',json={'period':'2026-07','theme':'nature'}).json
    assert {r['id'] for r in event['items']}==set(ids[:2])
    assert event['report']['other_events']==2 and event['report']['coarse_dates'] is False
    period=write(a,'post','/api/films/preview',json={'period':'2026-07','theme':'nature','story_mode':'period'}).json
    assert {r['id'] for r in period['items']}==set(ids)
    assert write(a,'post','/api/films/preview',json={'period':'2026-07','prompt':'x'*601}).status_code==400


def test_real_chinese_embedding_and_negative_brief():
    query=text_vector('孩子在海边玩耍')
    relevant=float(query@text_vector('一家人在沙滩上陪孩子玩耍'))
    unrelated=float(query@text_vector('软件安装与办公表格'))
    assert relevant>.9 and relevant>unrelated+.08
    positive,negative=split_brief('海边家庭旅行，优先孩子和日落；不要餐桌。闭眼不要选，按时间顺序')
    assert '海边家庭旅行' in positive and '日落' in positive and negative==['餐桌'] and '闭眼' not in positive


def test_photo_notes_affect_brief_selection_and_negative_notes_are_excluded(system,monkeypatch):
    app,a,b=system;ids=[write(a,'post','/api/upload?name=a.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','blue','green']]
    for aid,note in zip(ids,['一家人在沙滩上陪孩子玩耍','孩子在海边玩沙子','一家人围坐在餐桌前']):write(a,'patch','/api/assets/'+aid,json={'note':note})
    samples={aid:values(number) for aid,number in zip(ids,[0,0xFFFFFFFFFFFFFFFF,0xAAAAAAAAAAAAAAAA])}
    monkeypatch.setattr(curation,'analyze_asset',lambda conn,row:samples[row['id']])
    preview=write(a,'post','/api/films/preview',json={'period':'2026-07','theme':'nature','prompt':'孩子在海边玩耍，不要餐桌'}).json
    assert {r['id'] for r in preview['items']}==set(ids[:2])
    assert all('备注匹配' in r['reason'] for r in preview['items']) and preview['report']['theme']==1
