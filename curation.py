"""Offline thematic selection; renderers use originals, never these analysis previews."""
from functools import lru_cache
from datetime import datetime,timedelta
import json
import sqlite3
from pathlib import Path
import threading

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image,ImageOps
from tokenizers import Tokenizer
from film_rules import check_eyes,text_vector,split_brief

ROOT=Path(__file__).resolve().parent
THEMES={'people':'人物故事','nature':'自然风景','travel':'旅行记忆','favorites':'我的珍藏'}
TEMPLATES={
    'warm':{'name':'温柔日常','seconds':4,'color':'','description':'原色画面、缓慢推近、轻柔淡入淡出'},
    'postcard':{'name':'旅行明信片','seconds':3,'color':'eq=saturation=1.06:contrast=1.02,','description':'明快节奏、风景留白、保留视频原声'},
    'cinema':{'name':'时光胶片','seconds':5,'color':'eq=saturation=0.88:contrast=1.03,','description':'低饱和色调、舒缓节奏、电影片头'}
}
PROMPTS={
    'people':['a real photograph of a family spending time together','a portrait photograph of a smiling person','a candid photo of children playing'],
    'nature':['a beautiful landscape photograph of mountains and a lake','a beautiful photograph of the ocean and beach at sunset','a photograph of flowers, trees and a garden'],
    'travel':['a travel photograph of a city and architecture','a vacation photo of people visiting a landmark','a scenic photograph of a street and historic buildings'],
    'event':['a real photograph of a wedding, birthday or family celebration'],
    'pet':['a real photograph of a pet dog or cat'],
    'food':['a real photograph of food on a table'],
    'document':['a scanned document with printed text','a photograph of a receipt or ticket','a page of notes, a slide presentation or a textbook'],
    'screenshot':['a screenshot of a mobile phone app or chat conversation','a computer screenshot with text and interface buttons','a screenshot of a social media post'],
    'game':['a screenshot of a video game or animated cartoon'],
    'art':['a digital illustration, a drawing or graphic design','a poster with text and a QR code'],
    'other':['an unrecognizable dark blurry image','a photograph of an object on a plain background']
}


class Classifier:
    def __init__(self):
        from runtime import MODEL_DIR
        folder=MODEL_DIR/'clip'
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.vision=ort.InferenceSession(str(folder/'vision_model_quantized.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        text=ort.InferenceSession(str(folder/'text_model_quantized.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        tokenizer=Tokenizer.from_file(str(folder/'tokenizer.json'))
        tokenizer.enable_truncation(max_length=77);tokenizer.enable_padding(pad_id=49407,length=77)
        self.labels=list(PROMPTS);self.features=[]
        for prompts in PROMPTS.values():
            ids=np.array([e.ids for e in tokenizer.encode_batch(prompts)],np.int64)
            vectors=text.run(None,{'input_ids':ids})[0]
            vectors/=np.linalg.norm(vectors,axis=1,keepdims=True)
            feature=vectors.mean(axis=0);self.features.append(feature/np.linalg.norm(feature))
        self.features=np.array(self.features,np.float32)

    def analyze(self,image):
        image=ImageOps.fit(image,(224,224),method=Image.Resampling.BICUBIC)
        pixels=np.asarray(image,dtype=np.float32)/255
        pixels=(pixels-np.array([.48145466,.4578275,.40821073]))/np.array([.26862954,.26130258,.27577711])
        embedding=self.vision.run(None,{'pixel_values':pixels.transpose(2,0,1)[None].astype(np.float32)})[0][0]
        embedding/=np.linalg.norm(embedding)
        return dict(zip(self.labels,map(float,self.features@embedding))),embedding


_lock=threading.Lock()
@lru_cache(maxsize=1)
def classifier():
    return Classifier()


def analyze_asset(conn,row):
    source=Path(row['path'])
    if not source.is_file():raise ValueError('原片不在线')
    stat=source.stat();signature=f'{source}|{stat.st_size}|{stat.st_mtime_ns}'
    if row['kind']=='video' and (not row['thumb'] or not Path(row['thumb']).is_file()):
        raise ValueError('视频封面尚未准备好')
    cached=conn.execute('SELECT value FROM curation WHERE asset=?',(row['id'],)).fetchone()
    if cached:
        value=json.loads(cached[0])
        if value.get('version')==5 and value.get('signature')==signature:return value
    # Original dimensions are important; a small preview does not imply a small original.
    with Image.open(source if row['kind']=='image' else row['thumb']) as im:
        image=ImageOps.exif_transpose(im).convert('RGB');width,height=image.size;image.thumbnail((960,960))
        eyes=check_eyes(image) if row['kind']=='image' else {'checked':0,'closed':0,'openness':0}
        image.thumbnail((640,640))
        gray=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
        small=image.convert('L').resize((9,8))
        pixels=np.asarray(small);bits=(pixels[:,1:]>pixels[:,:-1]).reshape(-1)
        phash=sum(int(b)<<i for i,b in enumerate(bits))
        dct=cv2.dct(np.asarray(image.convert('L').resize((32,32)),dtype=np.float32))[:8,:8].reshape(-1)
        perceptual=sum(int(b)<<i for i,b in enumerate(dct>np.median(dct[1:])))
        with _lock:
            scores,embedding=classifier().analyze(image)
        result={'scores':scores,'width':width,'height':height,'blur':float(cv2.Laplacian(gray,cv2.CV_64F).var()),
                'contrast':float(gray.std()),'brightness':float(gray.mean()),'hash':str(phash),'phash':str(perceptual),
                'color':np.asarray(image,dtype=float).mean(axis=(0,1)).tolist(),'embedding':embedding.tolist(),
                'eyes':eyes,'version':5,'signature':signature}
    conn.execute('INSERT OR REPLACE INTO curation VALUES(?,?)',(row['id'],json.dumps(result)));conn.commit()
    return result


def duplicate_photo(left,right):
    a,av=left[:2];b,bv=right[:2]
    if a['kind']!='image' or b['kind']!='image':return False
    if a.get('hash') and a['hash']==b.get('hash'):return True
    if (int(av['hash'])^int(bv['hash'])).bit_count()>5:return False
    if (int(av.get('phash',av['hash']))^int(bv.get('phash',bv['hash']))).bit_count()>8:return False
    return float(np.linalg.norm(np.array(av.get('color',[0,0,0]))-np.array(bv.get('color',[0,0,0]))))<24


def filter_selected(conn,rows):
    ranked=[];closed=unavailable=0
    for row in rows:
        try:value=analyze_asset(conn,row)
        except (OSError,ValueError,TypeError):unavailable+=1;continue
        if row['kind']=='image' and value.get('eyes',{}).get('closed',0):closed+=1;continue
        score=min(value['blur'],500)/10000+value.get('eyes',{}).get('openness',0)*.03
        ranked.append((dict(row),value,score,''))
    ranked.sort(key=lambda item:item[2],reverse=True);selected=[];duplicates=0
    for item in ranked:
        if any(duplicate_photo(item,old) for old in selected):duplicates+=1;continue
        selected.append(item)
    selected.sort(key=lambda item:item[0]['date'])
    return [item[0] for item in selected],{'closed':closed,'duplicates':duplicates,'unavailable':unavailable}


def event_groups(ranked):
    groups=[]
    for item in sorted(ranked,key=lambda r:r[0]['date']):
        date=item[0]['date']
        try:stamp=datetime.fromisoformat(date) if len(date)>=19 else None
        except ValueError:stamp=None
        if stamp is None:
            # Month-only backup dates cannot establish that files belong to one activity.
            key=date[:10]
            group=next((g for g in groups if g['coarse']==key),None)
            if not group:group={'coarse':key,'items':[],'last':None,'start':None};groups.append(group)
        elif groups and groups[-1]['last'] and stamp-groups[-1]['last']<=timedelta(hours=6) and stamp-groups[-1]['start']<=timedelta(hours=12):
            group=groups[-1];group['last']=stamp
        else:
            group={'coarse':None,'items':[],'last':stamp,'start':stamp};groups.append(group)
        group['items'].append(item)
    return groups


def curated_assets(conn,owner,period,theme='people',person=None,limit=30,prompt='',story_mode='event'):
    if theme not in THEMES:
        raise ValueError('请选择有效主题')
    query="SELECT a.* FROM assets a WHERE a.owner=? AND a.deleted_at IS NULL AND a.live_parent IS NULL AND a.purged_at IS NULL AND a.date LIKE ?"
    args=[owner,period+'%']
    if theme=='favorites':
        query+=' AND EXISTS(SELECT 1 FROM favorites f WHERE f.asset=a.id AND f.user=?)';args.append(owner)
    if person:
        query+=' AND EXISTS(SELECT 1 FROM faces f WHERE f.asset=a.id AND f.person=?)';args.append(person)
    rows=conn.execute(query+' ORDER BY a.date,a.id',args).fetchall()
    report={'pool_total':len(rows),'closed':0,'duplicates':0,'quality':0,'theme':0,'other_events':0,'unavailable':0,'coarse_dates':False}
    positive,negative=split_brief(prompt)
    target=text_vector(positive) if positive else None
    negatives=[(text,text_vector(text)) for text in negative]
    # Prefer annotated matches, then retain a time-distributed candidate pool.
    if len(rows)>240:
        annotated=[r for r in rows if r['note']][:240] if positive else []
        annotated=[r for r in annotated if float(text_vector(r['note'])@target)>.9][:80]
        pool={r['id']:r for r in annotated}
        for i in np.linspace(0,len(rows)-1,240-len(pool),dtype=int):pool[rows[i]['id']]=rows[i]
        rows=list(pool.values())
    ranked=[]
    for row in rows:
        try:
            value=analyze_asset(conn,row);s=value['scores']
        except (OSError,ValueError,TypeError):
            report['unavailable']+=1
            continue
        if row['kind']=='image' and value.get('eyes',{}).get('closed',0):report['closed']+=1;continue
        vector=np.array(value.get('embedding',[]))
        visual=float(vector@target) if target is not None and vector.size else 0
        note=row['note'] or '';note_score=float(text_vector(note)@target) if note and target is not None else 0
        note_match=note_score>.9
        base=max(s[k] for k in (('people','event') if theme=='people' else ('nature',) if theme=='nature' else ('travel','nature','people')))
        if any((text in note) or (vector.size and float(vector@negative_vector)>.27 and float(vector@negative_vector)>max(visual,base)+.035) for text,negative_vector in negatives):
            report['theme']+=1;continue
        bad=max(s[k] for k in ('document','screenshot','game','art','other'))
        allowed=base
        allowed=max(allowed,visual,.32 if note_match else 0)
        if theme!='favorites' and (allowed<.20 or allowed<bad+.015 or (target is not None and visual<.20 and not note_match)):
            report['theme']+=1
            continue
        if theme!='favorites' and (max(value['width'],value['height'])<(1000 if row['kind']=='image' else 720) or value['contrast']<9 or not 15<value['brightness']<245 or value['blur']<10):
            report['quality']+=1
            continue
        best=max(('people','nature','travel','event','pet','food'),key=lambda k:s[k])
        description={'people':'人物与生活','nature':'自然风景','travel':'旅行与建筑','event':'相聚时刻','pet':'宠物','food':'餐桌记忆'}[best]
        score=allowed+min(value['blur'],500)/10000+value.get('eyes',{}).get('openness',0)*.03+visual*.6+max(0,note_score)*.18
        reason=('你的珍藏' if theme=='favorites' else description+' · 清晰度通过')
        if value.get('eyes',{}).get('checked',0):reason+=' · 未检出明显闭眼'
        if note_match:reason+=' · 备注匹配：'+note[:50]
        elif target is not None and visual>=.20:reason+=' · 贴近导演要求'
        ranked.append((dict(row),value,score,reason))
    ranked.sort(key=lambda item:item[2],reverse=True)
    unique=[]
    for item in ranked:
        if any(duplicate_photo(item,old) for old in unique):report['duplicates']+=1;continue
        unique.append(item)
    if story_mode=='event' and unique:
        groups=event_groups(unique)
        viable=[g for g in groups if len(g['items'])>=2] or groups
        group=max(viable,key=lambda g:sum(sorted((i[2] for i in g['items']),reverse=True)[:5])/min(5,len(g['items']))+.015*min(len(g['items']),10))
        report['other_events']=len(unique)-len(group['items']);report['coarse_dates']=group['coarse'] is not None
        unique=sorted(group['items'],key=lambda item:item[2],reverse=True)
    selected=[];days={}
    for row,value,score,reason in unique:
        day=row['date'][:10]
        if story_mode=='period' and days.get(day,0)>=4:
            continue
        selected.append({'id':row['id'],'name':row['name'],'date':row['date'],'kind':row['kind'],'reason':reason})
        days[day]=days.get(day,0)+1
        if len(selected)>=limit:
            break
    selected.sort(key=lambda r:r['date'])
    report['analyzed']=len(rows)
    report['range']=[selected[0]['date'],selected[-1]['date']] if selected else []
    return selected,report


def music_catalog(data,owner=None):
    source=ROOT/'music-library.json'
    tracks=[r for r in json.loads(source.read_text(encoding='utf-8')) if (Path(data)/'music'/r['file']).is_file()]
    if owner:
        with sqlite3.connect(Path(data)/'memory.sqlite') as conn:
            for mid,title,path in conn.execute('SELECT id,title,path FROM music_tracks WHERE owner=? ORDER BY created DESC',(owner,)):
                if Path(path).is_file():
                    tracks.append({'id':mid,'title':title,'label':'我的音乐 · '+title,'author':'本地上传','license':'用户提供','source':'','file':str(Path(path).relative_to(Path(data)/'music'))})
    return tracks
