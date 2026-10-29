import hashlib
import uuid

import numpy as np
from PIL import Image,ImageDraw
import pytest

from processing import connection
from tests.test_app import system,write


def seed_people(app,owner,names=('角色甲','角色甲','角色甲','角色甲','角色乙','')):
    """Artificial profile icons only; no family media or recognition model required."""
    data=app.config['DATA_DIR'];conn=connection(data);ids=[]
    for number,name in enumerate(names):
        pid=uuid.uuid4().hex;aid='test-'+pid;fid='face-'+pid
        icon=Image.new('RGB',(240,240),['#d6bda7','#a7bdcf','#b1c3a0','#ceadb9','#bfb1d0','#c9c8ab'][number%6])
        draw=ImageDraw.Draw(icon);draw.ellipse((84,38,156,110),fill='#fffdf4');draw.ellipse((52,119,188,260),fill='#fffdf4')
        path=data/'uploads'/(aid+'.jpg');icon.save(path)
        embedding=np.zeros(128,np.float32);embedding[number%128]=1
        conn.execute('INSERT INTO assets(id,owner,name,path,thumb,kind,date,date_source,bytes,source,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (aid,owner,'测试图标-'+str(number)+'.jpg',str(path),str(path),'image','2026-10-01 10:00:00','测试',path.stat().st_size,'upload','测试备注'))
        conn.execute('INSERT INTO people(id,owner,name,embedding) VALUES(?,?,?,?)',(pid,owner,name,embedding.tobytes()))
        conn.execute('INSERT INTO faces VALUES(?,?,?,?,?)',(fid,aid,pid,str(path),embedding.tobytes()));ids.append(pid)
    # Two groups can occur in the same photograph: merged counts must stay distinct.
    if len(ids)>1:
        first=conn.execute('SELECT asset,crop,embedding FROM faces WHERE person=?',(ids[0],)).fetchone()
        conn.execute('INSERT INTO faces VALUES(?,?,?,?,?)',('extra-'+uuid.uuid4().hex,first['asset'],ids[1],first['crop'],first['embedding']))
    conn.commit();conn.close();return ids


def test_batch_merge_preserves_target_assets_notes_and_other_groups(system):
    app,a,b=system;owner=a.get('/api/session').json['user']['id'];ids=seed_people(app,owner)
    conn=connection(app.config['DATA_DIR']);rows=conn.execute('SELECT * FROM assets').fetchall()
    before={r['id']:hashlib.sha256(open(r['path'],'rb').read()).hexdigest() for r in rows}
    cover=next(p['cover'] for p in a.get('/api/people').json['items'] if p['id']==ids[1])
    response=write(a,'post','/api/people/merge',json={'ids':ids[:4],'target':ids[1]})
    assert response.status_code==200 and response.json=={'id':ids[1],'name':'角色甲','count':4,'merged':3}
    assert {r['id'] for r in a.get('/api/people').json['items']}=={ids[1],ids[4],ids[5]}
    assert next(p['cover'] for p in a.get('/api/people').json['items'] if p['id']==ids[1])==cover
    assert conn.execute('SELECT count(*) FROM faces WHERE person=?',(ids[1],)).fetchone()[0]==5
    assert a.get('/api/assets?person='+ids[1]).json['total']==4
    assert a.get('/api/assets').json['total']==len(rows)
    assert all(r['note']=='测试备注' and hashlib.sha256(open(r['path'],'rb').read()).hexdigest()==before[r['id']] for r in conn.execute('SELECT * FROM assets'))
    assert write(a,'patch','/api/faces/'+cover,json={'person':'new'}).status_code==200
    fallback=next(p['cover'] for p in a.get('/api/people').json['items'] if p['id']==ids[1])
    assert fallback and fallback!=cover and a.get('/media/face/'+fallback).status_code==200
    conn.close()


@pytest.mark.parametrize('invalid',['foreign','missing','hidden'])
def test_batch_merge_validates_every_group_before_modifying_anything(system,invalid):
    app,a,b=system;owner=a.get('/api/session').json['user']['id'];ids=seed_people(app,owner)
    other=seed_people(app,b.get('/api/session').json['user']['id'],('另一个账号',))[0]
    conn=connection(app.config['DATA_DIR'])
    if invalid=='hidden':conn.execute('UPDATE people SET hidden=1 WHERE id=?',(ids[2],));conn.commit()
    before=[tuple(r) for r in conn.execute('SELECT id,person FROM faces ORDER BY id')]
    selected=[ids[0],ids[1],other if invalid=='foreign' else 'missing' if invalid=='missing' else ids[2]]
    assert write(a,'post','/api/people/merge',json={'ids':selected,'target':ids[0]}).status_code==409
    assert [tuple(r) for r in conn.execute('SELECT id,person FROM faces ORDER BY id')]==before
    assert conn.execute('SELECT count(*) FROM people WHERE owner=?',(owner,)).fetchone()[0]==6
    assert write(b,'post','/api/people/merge',json={'ids':ids[:2],'target':ids[0]}).status_code==409
    conn.close()


def test_batch_merge_rejects_invalid_selections_and_stale_resubmission(system):
    app,a,b=system;ids=seed_people(app,a.get('/api/session').json['user']['id'])
    for body in [{'ids':[ids[0]],'target':ids[0]},{'ids':[ids[0],ids[0]],'target':ids[0]},
                 {'ids':ids[:2],'target':ids[2]},{'ids':[{},ids[0]],'target':ids[0]},
                 {'ids':['id-'+str(i) for i in range(201)],'target':'id-0'},[]]:
        assert write(a,'post','/api/people/merge',json=body).status_code==400
    body={'ids':ids[:4],'target':ids[0]}
    assert write(a,'post','/api/people/merge',json=body).status_code==200
    assert write(a,'post','/api/people/merge',json=body).status_code==409
    assert len(a.get('/api/people').json['items'])==3
