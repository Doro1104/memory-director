"""Local face-expression checks and Chinese/English brief embeddings."""
from functools import lru_cache
from pathlib import Path
import re
import threading

import mediapipe as mp
import numpy as np
import onnxruntime as ort
from safetensors.numpy import load_file
from tokenizers import Tokenizer

from runtime import MODEL_DIR as MODELS
_eye_lock=threading.Lock()
_text_lock=threading.Lock()


@lru_cache(maxsize=1)
def face_landmarker():
    options=mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=(MODELS/'face_landmarker.task').read_bytes()),
        num_faces=12,min_face_detection_confidence=.6,min_face_presence_confidence=.6,
        output_face_blendshapes=True)
    return mp.tasks.vision.FaceLandmarker.create_from_options(options)


def check_eyes(image):
    pixels=np.asarray(image,dtype=np.uint8)
    with _eye_lock:
        result=face_landmarker().detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(pixels)))
    height,width=pixels.shape[:2];checked=closed=0;openness=[]
    for landmarks,shapes in zip(result.face_landmarks,result.face_blendshapes):
        xs=[p.x*width for p in landmarks];ys=[p.y*height for p in landmarks]
        if min(max(xs)-min(xs),max(ys)-min(ys))<70:
            continue
        blend={s.category_name:s.score for s in shapes}
        left=blend.get('eyeBlinkLeft',0);right=blend.get('eyeBlinkRight',0)
        checked+=1;closed+=int(left>=.65 and right>=.65)
        openness.append(1-(left+right)/2)
    return {'detected':len(result.face_landmarks),'checked':checked,'closed':closed,
            'openness':sum(openness)/len(openness) if openness else 0}


class BriefEncoder:
    def __init__(self):
        folder=MODELS/'multilingual'
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        self.model=ort.InferenceSession(str(folder/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        self.tokenizer=Tokenizer.from_file(str(folder/'tokenizer.json'))
        self.tokenizer.enable_truncation(max_length=128);self.tokenizer.enable_padding(pad_id=0)
        self.weight=load_file(folder/'dense.safetensors')['linear.weight']

    def encode(self,text):
        encoded=self.tokenizer.encode(text)
        ids=np.array([encoded.ids],np.int64);mask=np.array([encoded.attention_mask],np.int64)
        hidden=self.model.run(None,{'input_ids':ids,'attention_mask':mask})[0]
        pooled=(hidden*mask[:,:,None]).sum(axis=1)/np.maximum(mask.sum(axis=1,keepdims=True),1)
        vector=(pooled@self.weight.T)[0]
        return vector/max(float(np.linalg.norm(vector)),1e-8)


@lru_cache(maxsize=1)
def brief_encoder():
    return BriefEncoder()


@lru_cache(maxsize=512)
def text_vector(text):
    with _text_lock:
        return brief_encoder().encode(text[:1000])


def split_brief(prompt):
    positive=[];negative=[]
    for clause in re.split(r'[，,。；;\n]+',prompt):
        clause=clause.strip()
        if not clause:continue
        match=re.match(r'^(?:请|尽量|务必|最好|一定)?(?:不要|不选|排除|避免|不包含|不需要|去掉|别选)(.+)',clause)
        if match:
            value=match[1].strip()
            if not re.search(r'闭眼|重复|模糊|截图|文档',value):negative.append(value)
        elif not re.search(r'闭眼|重复|去重|清晰度|按时间|时间顺序|连贯|同一活动|同一时段|配乐|音乐|秒|720|1080',clause):
            positive.append(re.sub(r'^(?:优先|突出|围绕|选取|重点展示|希望|请)', '',clause))
    return '，'.join(positive),negative
