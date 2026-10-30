import copy
import io
import json
from pathlib import Path
import subprocess
import urllib.error

from PIL import Image
import pytest
import curation
import director
import director_provider
from processing import connection,ffmpeg,render_film
from tests.test_app import system,write,picture


def pool():
    return [{'asset_id':str(i),'kind':'image','date':f'2026-07-0{i+1}','available_frames':240,'note':'','evidence':'synthetic'} for i in range(3)]


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(fps=24),lambda p:p.update(version=True),lambda p:p.update(command='delete'),
    lambda p:p['clips'][0].update(asset_id='not-selected'),lambda p:p['clips'][1].update(asset_id='0'),
    lambda p:p['clips'][0].update(in_frame=1),lambda p:p['clips'][0].update(frames=True),
    lambda p:p['clips'][0].update(frames=300),lambda p:p['clips'][0].update(frames=119),
    lambda p:p['clips'][0].update(role='ending'),lambda p:p['clips'][0].update(audio='execute'),
    lambda p:p['clips'][0].update(path='untrusted'),lambda p:p['clips'].reverse(),
])
def test_plan_rejects_unsafe_or_nonconforming_edits(mutate):
    candidates=pool();plan=director.local_plan(candidates,360);mutate(plan)
    with pytest.raises(ValueError):director.validate(plan,candidates,360)


def test_local_revision_and_video_bounds():
    candidates=pool();plan=director.local_plan(candidates,360)
    revision=director.local_plan(candidates,360,plan,'开头短一点；全部静音')
    assert revision['clips'][0]['frames']<plan['clips'][0]['frames']
    assert sum(c['frames'] for c in revision['clips'])==360
    assert all(c['audio']=='mute' for c in revision['clips'])
    revision=director.local_plan(candidates,360,plan,'去掉第 2 个镜头')
    assert len(revision['clips'])==2 and revision['clips'][1]['asset_id']=='2'
    manual=copy.deepcopy(plan)
    for clip,frames in zip(manual['clips'],[90,120,150]):clip['frames']=frames
    assert [c['frames'] for c in director.local_plan(candidates,360,manual,'全部静音')['clips']]==[90,120,150]
    candidates[0].update(kind='video',available_frames=150)
    plan=director.local_plan(candidates,360);plan['clips'][0]['in_frame']=149
    with pytest.raises(ValueError):director.validate(plan,candidates,360)
    assert director.target_from(pool(),0,'总时长 12 秒')==360
    assert director.target_from(pool(),0,'每个镜头 3 秒')==360
    with pytest.raises(ValueError):director.local_plan(pool(),1000)


@pytest.fixture
def workflow(system,monkeypatch):
    application,a,b=system
    monkeypatch.setattr(curation,'filter_selected',lambda conn,rows:(rows,{'closed':0,'duplicates':0,'unavailable':0}))
    ids=[write(a,'post','/api/upload?name=private-name.jpg&modified=1782864000000',data=picture(c)).json['id'] for c in ['red','blue','green']]
    for aid in ids:write(a,'patch','/api/assets/'+aid,json={'note':'private-note-for-consent'})
    response=write(a,'post','/api/director/drafts',json={'period':'2026-07','ids':ids,'music':'none'})
    assert response.status_code==200,response.json
    return application,a,b,response.json


def test_drafts_ownership_snapshot_revisions_and_cloud_consent(workflow,monkeypatch):
    application,a,b,draft=workflow;did=draft['id'];url='/api/director/drafts/'+did
    assert b.get(url).status_code==404 and b.get('/api/director/drafts').json['items']==[]
    assert write(b,'post',url+'/render',json={'revision':1}).status_code==404
    calls=[]
    def fake_cloud(provider,payload):
        calls.append(payload);return copy.deepcopy(draft['plan'])
    monkeypatch.setattr(director_provider,'plan',fake_cloud)
    req={'revision':1,'engine':'openai','instruction':'温柔一些'}
    assert write(a,'post',url+'/revise',json=req).status_code==403 and not calls
    proof=write(a,'post',url+'/outbound',json=req).json
    encoded=json.dumps(proof['payload'])
    assert 'private-note-for-consent' in encoded
    assert 'private-name.jpg' not in encoded and 'source_stamps' not in encoded and 'uploads' not in encoded
    req.update(consent=True,digest=proof['digest'])
    bad={**req,'instruction':'changed-after-review'}
    assert write(a,'post',url+'/revise',json=bad).status_code==403 and not calls
    revised=write(a,'post',url+'/revise',json=req)
    assert revised.status_code==200 and revised.json['revision']==2 and calls==[proof['payload']]
    assert write(a,'post',url+'/revise',json=req).status_code==409 and len(calls)==1
    assert write(a,'post',url+'/render',json={'revision':1}).status_code==409
    assert write(a,'patch',url,json={'revision':1,'plan':draft['plan']}).status_code==409
    conn=connection(application.config['DATA_DIR']);assert conn.execute('SELECT count(*) FROM director_revisions').fetchone()[0]==2;conn.close()


def test_invalid_cloud_preserves_draft_and_unlocks(workflow,monkeypatch):
    application,a,b,draft=workflow;url='/api/director/drafts/'+draft['id'];proof=write(a,'post',url+'/outbound',json={'revision':1}).json
    invalid=copy.deepcopy(draft['plan']);invalid['clips'][0]['in_frame']=9000
    monkeypatch.setattr(director_provider,'plan',lambda *args:invalid)
    response=write(a,'post',url+'/revise',json={'revision':1,'engine':'anthropic','consent':True,'digest':proof['digest']})
    assert response.status_code==400
    assert a.get(url).json['revision']==1 and a.get(url).json['plan']==draft['plan']
    assert write(a,'post',url+'/revise',json={'revision':1,'instruction':'开头短一点'}).status_code==200


def test_changed_deleted_assets_stop_and_report_excludes_private_text(workflow):
    application,a,b,draft=workflow;url='/api/director/drafts/'+draft['id']
    result=write(a,'post',url+'/render',json={'revision':1});assert result.status_code==200
    fid=result.json['id'];feedback='/api/director/films/'+fid
    assert b.get(feedback+'/plan').status_code==404
    assert write(b,'post',feedback+'/feedback',json={}).status_code==404
    assert write(a,'post',feedback+'/feedback',json={'scores':{'story':True,'selection':4,'pace':3,'sound':2}}).status_code==400
    assert write(a,'post',feedback+'/feedback',json={'scores':{'story':4,'selection':4,'pace':3,'sound':2},'issues':['pace'],'comment':'private-feedback-note'}).status_code==200
    report=a.get(feedback+'/feedback-report')
    for secret in ('private-feedback-note','private-name','private-note',draft['id'],draft['candidates'][0]['asset_id']):assert secret not in report.text
    assert report.json['issues']==['pace']
    conn=connection(application.config['DATA_DIR']);path=Path(conn.execute('SELECT path FROM assets LIMIT 1').fetchone()[0]);path.write_bytes(path.read_bytes()+b'changed');conn.close()
    assert write(a,'post',url+'/render',json={'revision':1}).status_code==409


@pytest.mark.parametrize('provider',['openai','anthropic'])
def test_provider_fixed_transport_schema_and_no_secret_echo(monkeypatch,provider):
    monkeypatch.setenv('MEMORY_DIRECTOR_'+provider.upper()+'_KEY','synthetic-key')
    monkeypatch.setenv('MEMORY_DIRECTOR_'+provider.upper()+'_MODEL','synthetic-structured-model')
    payload={'system':director.SYSTEM,'output_schema':director.PLAN_SCHEMA,'candidates':pool()}
    seen=[];expected=director.local_plan(pool(),360)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):
            body={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(expected)}]}]} if provider=='openai' else {'stop_reason':'end_turn','content':[{'type':'text','text':json.dumps(expected)}]}
            return json.dumps(body).encode()
    class Client:
        def open(self,req,timeout):seen.append(req);return Response()
    monkeypatch.setattr(director_provider.urllib.request,'build_opener',lambda *args:Client())
    assert director_provider.plan(provider,payload)==expected
    assert seen[0].full_url==director_provider.PROVIDERS[provider][0]
    request=json.loads(seen[0].data)
    assert 'synthetic-key' not in json.dumps(request) and 'tools' not in request
    if provider=='openai':assert request['store'] is False and request['text']['format']['schema']==director.PLAN_SCHEMA
    else:assert request['output_config']['format']['schema']==director.PLAN_SCHEMA
    class Failure:
        def open(self,req,timeout):raise urllib.error.HTTPError(req.full_url,401,'private-note',{},io.BytesIO(b'private-note synthetic-key'))
    monkeypatch.setattr(director_provider.urllib.request,'build_opener',lambda *args:Failure())
    with pytest.raises(ValueError) as error:director_provider.plan(provider,payload)
    assert 'private-note' not in str(error.value) and 'synthetic-key' not in str(error.value)


def test_real_timeline_render_frames_and_sources_unchanged(workflow):
    application,a,b,draft=workflow;data=application.config['DATA_DIR'];conn=connection(data)
    before={r['path']:Path(r['path']).read_bytes() for r in conn.execute('SELECT path FROM assets')}
    # All-image timeline exercises true frame counts without relying on generated previews.
    fid=write(a,'post','/api/director/drafts/'+draft['id']+'/render',json={'revision':1}).json['id']
    job=conn.execute("SELECT * FROM jobs WHERE kind='film' AND payload LIKE ?",('%'+fid+'%',)).fetchone();render_film(conn,data,job)
    film=conn.execute('SELECT * FROM films WHERE id=?',(fid,)).fetchone();assert film['status']=='done'
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',film['path']]))
    video=next(s for s in info['streams'] if s['codec_type']=='video')
    assert int(video['nb_read_frames'])==draft['target_frames']
    assert (video['width'],video['height'],video['r_frame_rate'],video['pix_fmt'])==(1920,1080,'30/1','yuv420p')
    assert abs(float(info['format']['duration'])-draft['target_frames']/30)<.1
    assert all(Path(path).read_bytes()==content for path,content in before.items())
    assert not (data/'jobs'/job['id']).exists()
    conn.close()


@pytest.mark.parametrize('audio',['original','mute'])
def test_real_video_trim_audio_and_music(workflow,audio):
    import cv2
    import numpy as np
    application,a,b,old=workflow;data=application.config['DATA_DIR']
    source=data/'synthetic-video.mp4'
    ffmpeg(['-f','lavfi','-i','color=c=red:s=320x240:r=30:d=2',
            '-f','lavfi','-i','color=c=green:s=320x240:r=30:d=2',
            '-f','lavfi','-i','color=c=blue:s=320x240:r=30:d=2',
            '-f','lavfi','-i','sine=frequency=440:duration=6',
            '-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
            '-map','[v]','-map','3:a','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',source])
    video=write(a,'post','/api/upload?name=synthetic-video.mp4&modified=1782864000000',data=source.read_bytes()).json['id']
    music='none'
    if audio=='original':
        track=data/'synthetic-music.wav';ffmpeg(['-f','lavfi','-i','sine=frequency=220:duration=1',track])
        music=write(a,'post','/api/music?name=synthetic-music.wav',data=track.read_bytes()).json['id']
    ids=[c['asset_id'] for c in old['candidates'][:2]]+[video]
    response=write(a,'post','/api/director/drafts',json={'period':'2026-07','ids':ids,'music':music,'resolution':'720'})
    assert response.status_code==200,response.json
    draft=response.json;plan=draft['plan'];at=0;video_start=0
    for clip in plan['clips']:
        clip['frames']=60
        if clip['asset_id']==video:clip['in_frame']=60;clip['audio']=audio;video_start=at
        at+=60
    url='/api/director/drafts/'+draft['id']
    edited=write(a,'patch',url,json={'revision':1,'plan':plan,'target_seconds':6});assert edited.status_code==200,edited.json
    fid=write(a,'post',url+'/render',json={'revision':2}).json['id']
    conn=connection(data);job=conn.execute("SELECT * FROM jobs WHERE kind='film' AND payload LIKE ?",('%'+fid+'%',)).fetchone()
    original=source.read_bytes();render_film(conn,data,job)
    film=conn.execute('SELECT * FROM films WHERE id=?',(fid,)).fetchone();cap=cv2.VideoCapture(film['path'])
    cap.set(cv2.CAP_PROP_POS_FRAMES,video_start+30);ok,frame=cap.read();cap.release();assert ok
    blue,green,red=frame.mean(axis=(0,1));assert green>red*2 and green>blue*2
    samples=subprocess.check_output([__import__('imageio_ffmpeg').get_ffmpeg_exe(),'-v','error','-i',film['path'],'-f','f32le','-ac','1','-ar','48000','-'])
    values=np.frombuffer(samples,dtype='<f4');segment=values[int((video_start/30+.5)*48000):int((video_start/30+1.3)*48000)]
    rms=float(np.sqrt(np.mean(segment**2)))
    assert rms>.02 if audio=='original' else rms<.001
    assert np.max(np.abs(values))<1.0 and source.read_bytes()==original
    conn.close()
