"""Account-scoped review -> plan -> consent -> render -> feedback workflow."""
import io
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from flask import Blueprint,abort,g,jsonify,request,send_file
from director import FPS,RULES_VERSION,ISSUES,integer,local_plan,target_from,validate,outbound,digest,feedback_report
import director_provider


def register(app,db,login_required,data,validate_options):
    bp=Blueprint('director',__name__)
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS director_drafts(id TEXT PRIMARY KEY,owner TEXT REFERENCES users(id),revision INTEGER,busy REAL DEFAULT 0,value TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS director_revisions(draft TEXT REFERENCES director_drafts(id),revision INTEGER,value TEXT,PRIMARY KEY(draft,revision));
        CREATE TABLE IF NOT EXISTS director_feedback(film TEXT PRIMARY KEY REFERENCES films(id),owner TEXT REFERENCES users(id),value TEXT);
        ''');db().commit()

    def body():
        if request.content_length and request.content_length>150000:abort(413,'剪辑方案请求过大')
        value=request.get_json()
        if not isinstance(value,dict):abort(400,'需要 JSON 对象')
        return value

    def draft(did):
        row=db().execute('SELECT * FROM director_drafts WHERE id=? AND owner=?',(did,g.user['id'])).fetchone()
        if not row:abort(404)
        return row,json.loads(row['value'])

    def owned_assets(value,only=None):
        rows=[]
        for candidate in value['candidates']:
            if only is not None and candidate['asset_id'] not in only:continue
            row=db().execute('SELECT * FROM assets WHERE id=? AND owner=? AND deleted_at IS NULL AND purged_at IS NULL AND live_parent IS NULL',(candidate['asset_id'],g.user['id'])).fetchone()
            if not row or not Path(row['path']).is_file():abort(409,'候选素材已删除、移走或不在线，请重新选片')
            stat=Path(row['path']).stat()
            if value['source_stamps'][row['id']]!=[stat.st_size,stat.st_mtime_ns]:abort(409,'候选原文件已改变，请重新选片')
            rows.append(row)
        return rows

    def result(row,value):
        return dict(id=row['id'],revision=row['revision'],**value)

    def save(did,row,value,busy=0):
        revision=row['revision']+1
        changed=db().execute('UPDATE director_drafts SET revision=?,value=?,busy=0 WHERE id=? AND owner=? AND revision=? AND busy=?',
                             (revision,json.dumps(value,ensure_ascii=False),did,g.user['id'],row['revision'],busy))
        if not changed.rowcount:db().rollback();abort(409,'方案已在另一个页面修改，请重新打开')
        db().execute('INSERT INTO director_revisions VALUES(?,?,?)',(did,revision,json.dumps(value,ensure_ascii=False)));db().commit()
        return jsonify(id=did,revision=revision,**value)

    @bp.errorhandler(ValueError)
    def invalid_director(error):
        # Validation messages are application-authored; external provider bodies never propagate.
        return jsonify(error=str(error)),400

    @bp.get('/api/director/status')
    @login_required
    def status():
        return jsonify(providers=director_provider.status(),rules_version=RULES_VERSION)

    @bp.get('/api/director/drafts')
    @login_required
    def drafts():
        rows=db().execute('SELECT id,revision,value FROM director_drafts WHERE owner=? ORDER BY created DESC LIMIT 20',(g.user['id'],)).fetchall()
        return jsonify(items=[{'id':r['id'],'revision':r['revision'],'title':json.loads(r['value'])['title']} for r in rows])

    @bp.post('/api/director/drafts')
    @login_required
    def prepare():
        from curation import filter_selected
        import imageio_ffmpeg
        value=body();options=validate_options(value);period=value.get('period','');ids=value.get('ids')
        if not isinstance(period,str) or not re.fullmatch(r'(19|20)\d{2}(-(0[1-9]|1[0-2]))?',period):abort(400,'请选择年份或月份')
        if not isinstance(ids,list) or not 2<=len(ids)<=30 or any(not isinstance(a,str) for a in ids) or len(set(ids))!=len(ids):abort(400,'请先选择 2–30 份素材')
        rows=[]
        for aid in ids:
            row=db().execute('SELECT * FROM assets WHERE id=? AND owner=? AND date LIKE ? AND deleted_at IS NULL AND purged_at IS NULL AND live_parent IS NULL',(aid,g.user['id'],period+'%')).fetchone()
            if not row:abort(400,'候选素材不属于当前账号或时期')
            rows.append(row)
        rows,removed=filter_selected(db(),rows)
        if any(removed.values()):abort(409,'候选中存在明显闭眼、重复或不可用素材，请重新预览')
        candidates=[]
        for row in sorted(rows,key=lambda r:(r['date'],r['id'])):
            available=240
            if row['kind']=='video':
                probe=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',row['path']],capture_output=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                match=re.search(rb'Duration: (\d+):(\d+):([\d.]+)',probe.stderr)
                if not match:abort(409,'视频时长无法读取，请先完成兼容处理')
                seconds=int(match[1])*3600+int(match[2])*60+float(match[3]);available=max(0,int(seconds*FPS)-1)
                if available<30:abort(400,'本版选片视频至少需要约 1 秒，请排除此短片')
            cached=db().execute('SELECT value FROM curation WHERE asset=?',(row['id'],)).fetchone()
            analysis=json.loads(cached[0]) if cached else {};scores=analysis.get('scores',{})
            labels={'people':'人物','nature':'自然','travel':'旅行','event':'相聚','pet':'宠物','food':'食物'}
            label=max(labels,key=lambda k:scores.get(k,0)) if scores else None
            evidence=('本地画面分类倾向：'+labels[label] if label else '没有可靠画面主题标签')+'；不代表身份、景别或动作连续性已确认'
            candidates.append({'asset_id':row['id'],'kind':row['kind'],'date':row['date'],'available_frames':available,'note':(row['note'] or '')[:400],'evidence':evidence})
        target=target_from(candidates,value.get('target_seconds',0),options['prompt'])
        stamps={r['id']:[Path(r['path']).stat().st_size,Path(r['path']).stat().st_mtime_ns] for r in rows}
        assets=[{k:r[k] for k in ('id','owner','name','kind','date','date_source','bytes','shared','source','note')} for r in rows]
        value={'title':str(value.get('title') or period+' · 时光来信')[:60],'period':period,'options':options,'target_frames':target,'source_stamps':stamps,
               'candidates':candidates,'assets':assets,'plan':local_plan(candidates,target,instruction=options['prompt']),'engine':'local'}
        did=uuid.uuid4().hex;encoded=json.dumps(value,ensure_ascii=False)
        db().execute('INSERT INTO director_drafts(id,owner,revision,value,created) VALUES(?,?,?,?,?)',(did,g.user['id'],1,encoded,time.time()))
        db().execute('INSERT INTO director_revisions VALUES(?,?,?)',(did,1,encoded));db().commit()
        return jsonify(id=did,revision=1,**value)

    @bp.get('/api/director/drafts/<did>')
    @login_required
    def get_draft(did):
        row,value=draft(did);return jsonify(result(row,value))

    @bp.post('/api/director/drafts/<did>/outbound')
    @login_required
    def disclosure(did):
        row,value=draft(did);req=body()
        if req.get('revision')!=row['revision']:abort(409,'方案版本已改变')
        payload=outbound(value,req.get('instruction',''))
        return jsonify(payload=payload,digest=digest(payload),revision=row['revision'])

    @bp.post('/api/director/drafts/<did>/revise')
    @login_required
    def revise(did):
        row,value=draft(did);req=body()
        if req.get('revision')!=row['revision']:abort(409,'方案版本已改变，请重新打开')
        owned_assets(value);engine=req.get('engine','local');instruction=req.get('instruction','')
        payload=outbound(value,instruction)
        if engine not in ('local',*director_provider.PROVIDERS):abort(400,'无效的编排方式')
        if engine!='local' and (req.get('consent') is not True or req.get('digest')!=digest(payload)):abort(403,'需要确认本次发送的完整文字摘要；没有发送任何内容')
        now=time.time()
        changed=db().execute('UPDATE director_drafts SET busy=? WHERE id=? AND revision=? AND busy<?',(now,did,row['revision'],now-120))
        db().commit()
        if not changed.rowcount:abort(409,'该方案正在处理，请勿重复提交')
        try:
            plan=local_plan(value['candidates'],value['target_frames'],value['plan'],instruction) if engine=='local' else director_provider.plan(engine,payload)
            value['plan']=validate(plan,value['candidates'],value['target_frames']);value['engine']=engine
            return save(did,row,value,now)
        finally:
            db().execute('UPDATE director_drafts SET busy=0 WHERE id=? AND busy=?',(did,now));db().commit()

    @bp.patch('/api/director/drafts/<did>')
    @login_required
    def edit(did):
        row,value=draft(did);req=body()
        if req.get('revision')!=row['revision'] or row['busy']>time.time()-120:abort(409,'方案已改变或正在处理，请稍后重新打开')
        owned_assets(value)
        seconds=req.get('target_seconds',value['target_frames']//FPS)
        target=integer(seconds,1,180,'目标秒数')*FPS
        value['plan']=validate(req.get('plan'),value['candidates'],target);value['target_frames']=target;value['engine']='manual'
        return save(did,row,value)

    @bp.post('/api/director/drafts/<did>/render')
    @login_required
    def render(did):
        from curation import filter_selected
        from processing import enqueue
        row,value=draft(did);req=body()
        if req.get('revision')!=row['revision']:abort(409,'请先确认最新版本的方案')
        validate(value['plan'],value['candidates'],value['target_frames'])
        used={c['asset_id'] for c in value['plan']['clips']};rows=owned_assets(value,used)
        _,removed=filter_selected(db(),[r for r in rows if r['id'] in used])
        if any(removed.values()):abort(409,'素材筛查结果已改变，请重新选片')
        options={**value['options'],'ids':[c['asset_id'] for c in value['plan']['clips']],
                 'director':value,'draft_id':did,'draft_revision':row['revision']}
        # Validate currently available music/output settings again before queuing.
        validate_options(options)
        fid=uuid.uuid4().hex
        db().execute('INSERT INTO films(id,owner,title,period,status,created,options) VALUES(?,?,?,?,?,?,?)',
                     (fid,g.user['id'],value['title'],value['period'],'queued',time.time(),json.dumps(options,ensure_ascii=False)))
        db().commit();enqueue(db(),g.user['id'],'film',{'film':fid})
        return jsonify(id=fid)

    def film(fid):
        row=db().execute('SELECT * FROM films WHERE id=? AND owner=? AND deleted_at IS NULL',(fid,g.user['id'])).fetchone()
        if not row:abort(404)
        value=json.loads(row['options'] or '{}').get('director')
        if not value:abort(400,'这是旧版影片，没有剪辑方案')
        return row,value

    @bp.get('/api/director/films/<fid>/plan')
    @login_required
    def film_plan(fid):
        row,value=film(fid);return jsonify(value)

    @bp.route('/api/director/films/<fid>/feedback',methods=['GET','POST'])
    @login_required
    def feedback(fid):
        row,value=film(fid)
        if request.method=='GET':
            saved=db().execute('SELECT value FROM director_feedback WHERE film=? AND owner=?',(fid,g.user['id'])).fetchone()
            return jsonify(json.loads(saved[0]) if saved else None)
        req=body();scores=req.get('scores');issues=req.get('issues',[]);comment=req.get('comment','')
        if not isinstance(scores,dict) or set(scores)!={'story','pace','selection','sound'}:abort(400,'请完成四项评分')
        for score in scores.values():integer(score,1,5,'评分')
        if not isinstance(issues,list) or len(issues)>len(ISSUES) or any(not isinstance(i,str) or i not in ISSUES for i in issues):abort(400,'无效的问题分类')
        if not isinstance(comment,str) or len(comment)>2000:abort(400,'本地反馈最多 2000 字')
        saved={'scores':scores,'issues':sorted(set(issues)),'comment':comment}
        db().execute('INSERT INTO director_feedback VALUES(?,?,?) ON CONFLICT(film) DO UPDATE SET value=excluded.value',(fid,g.user['id'],json.dumps(saved,ensure_ascii=False)));db().commit()
        return jsonify(ok=True)

    @bp.get('/api/director/films/<fid>/feedback-report')
    @login_required
    def report(fid):
        row,value=film(fid)
        feedback=db().execute('SELECT value FROM director_feedback WHERE film=? AND owner=?',(fid,g.user['id'])).fetchone()
        if not feedback:abort(400,'请先填写本地评分')
        report=feedback_report(value['plan'],json.loads(feedback[0]),value['engine']);output=io.BytesIO(json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        return send_file(output,mimetype='application/json',as_attachment=True,download_name='memory-director-feedback.json')

    app.register_blueprint(bp)
