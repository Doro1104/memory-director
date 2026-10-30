'use strict';
let directorDraft=null,directorDirty=false,directorBusy=false,directorProof=null,directorFeedbackFilm=null;
const roles={opening:'开场 · 交代',development:'发展 · 生活',ending:'结尾 · 回味'};
function directorError(error){$('director-error').textContent=error.message||String(error);}
function directorLock(busy){directorBusy=busy;$('director-dialog').querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=busy);}
function directorMarkDirty(){directorDirty=true;$('director-dirty').hidden=false;directorProof=null;}
function directorShow(value){
  directorDraft=value;directorDirty=false;directorProof=null;$('director-dirty').hidden=true;$('director-error').textContent='';
  $('director-revision').textContent='方案 v'+value.revision+' · 规则 '+value.plan.rules_version+' · '+value.plan.clips.length+' 镜头';
  $('director-seconds').value=value.target_frames/30;$('director-summary').textContent=value.plan.summary;
  $('director-clips').replaceChildren();let at=0;
  value.plan.clips.forEach((clip,index)=>{
    const candidate=value.candidates.find(c=>c.asset_id===clip.asset_id);const asset=value.assets.find(a=>a.id===clip.asset_id);
    const card=node('article','','director-clip');card.dataset.asset=clip.asset_id;
    const picture=action('',()=>openAsset({...asset,owner_name:user.name}),'director-preview');picture.append(photo('/media/'+clip.asset_id+'/thumb','查看第 '+(index+1)+' 个镜头'));
    const text=node('div','','director-clip-body');text.append(node('strong',(index+1)+' · '+roles[clip.role]),node('small',(at/30).toFixed(2)+'–'+((at+clip.frames)/30).toFixed(2)+' 秒 · '+(candidate.kind==='video'?'视频':'照片')+' · '+dateLabel(candidate.date)),node('p',clip.reason,'small'));
    const fields=node('div','','director-clip-fields');
    for(const [key,label,value]of [['in_frame','原片入点（秒）',clip.in_frame/30],['frames','停留时长（秒）',clip.frames/30]]){
      const field=node('label',label);const input=document.createElement('input');input.type='number';input.step='0.0333333333';input.min=key==='in_frame'?'0':candidate.kind==='image'?'2':'1';input.max=key==='in_frame'?String(candidate.available_frames/30):candidate.kind==='image'?'8':'12';input.value=value;input.dataset.field=key;input.readOnly=key==='in_frame'&&candidate.kind==='image';input.addEventListener('input',directorMarkDirty);field.append(input);fields.append(field);
    }
    const sound=node('label','原片声音');const select=document.createElement('select');select.dataset.field='audio';select.append(new Option('保留原声','original'),new Option('静音','mute'));select.value=clip.audio;select.addEventListener('change',directorMarkDirty);sound.append(select);fields.append(sound);
    text.append(fields);card.append(picture,text);$('director-clips').append(card);at+=clip.frames;
  });
  $('director-plan-json').hidden=true;if(!$('director-dialog').open)$('director-dialog').showModal();$('director-dialog').scrollTop=0;
}
async function prepareDirector(settings){
  const value=await post('/api/director/drafts',{...settings,target_seconds:0});directorShow(value);
  const status=await api('/api/director/status');$('director-engine').replaceChildren(new Option('本地规则 · 不联网','local'));
  for(const provider of status.providers){const option=new Option(provider.label+' · '+(provider.configured?'仅文字编排':'主机尚未配置'),provider.id);option.disabled=!provider.configured;$('director-engine').append(option);}
}
function readDirectorPlan(){
  const plan=JSON.parse(JSON.stringify(directorDraft.plan));
  [...$('director-clips').children].forEach((card,i)=>{for(const key of ['in_frame','frames'])plan.clips[i][key]=Math.round(Number(card.querySelector('[data-field='+key+']').value)*30);plan.clips[i].audio=card.querySelector('[data-field=audio]').value;});return plan;
}
$('director-seconds').addEventListener('input',directorMarkDirty);
$('director-save').addEventListener('click',async()=>{
  if(directorBusy)return;directorLock(true);
  try{const value=await patch('/api/director/drafts/'+directorDraft.id,{revision:directorDraft.revision,target_seconds:Number($('director-seconds').value),plan:readDirectorPlan()});directorShow(value);toast('镜头调整已保存并通过规则校验');}catch(e){directorError(e);}finally{directorLock(false);}
});
$('director-export-plan').addEventListener('click',()=>{$('director-plan-json').hidden=!$('director-plan-json').hidden;$('director-plan-json').textContent=JSON.stringify(directorDraft.plan,null,2);});
$('director-external').addEventListener('click',async()=>{
  if(directorDirty)return directorError(new Error('请先保存镜头调整'));
  try{const proof=await post('/api/director/drafts/'+directorDraft.id+'/outbound',{revision:directorDraft.revision,instruction:$('director-instruction').value});$('director-external-task').value=JSON.stringify(proof.payload,null,2);$('director-external-dialog').showModal();}catch(e){directorError(e);}
});
$('director-external-copy').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText($('director-external-task').value);toast('已复制任务，请只交给你愿意授权的外部 AI');}catch{$('director-external-task').focus();$('director-external-task').select();toast('请使用系统复制；其中可能包含私人备注');}
});
$('director-import').addEventListener('click',async()=>{
  if(directorBusy||directorDirty)return directorError(new Error('请先保存镜头调整'));
  directorLock(true);
  try{const plan=JSON.parse($('director-import-json').value);const value=await patch('/api/director/drafts/'+directorDraft.id,{revision:directorDraft.revision,target_seconds:directorDraft.target_frames/30,plan});directorShow(value);$('director-import-json').value='';toast('外部方案已通过规则校验，尚未生成影片');}catch(e){directorError(e);}finally{directorLock(false);}
});
function invalidateDirectorConsent(){directorProof=null;$('director-consent-check').checked=false;$('director-cloud-confirm').disabled=true;}
$('director-instruction').addEventListener('input',invalidateDirectorConsent);$('director-engine').addEventListener('change',invalidateDirectorConsent);
$('director-apply').addEventListener('click',async()=>{
  if(directorDirty)return directorError(new Error('请先保存镜头调整'));
  if(directorBusy)return;const instruction=$('director-instruction').value,engine=$('director-engine').value;
  directorLock(true);
  try{
    if(engine==='local'){directorShow(await post('/api/director/drafts/'+directorDraft.id+'/revise',{revision:directorDraft.revision,engine,instruction}));return;}
    const proof=await post('/api/director/drafts/'+directorDraft.id+'/outbound',{revision:directorDraft.revision,instruction});
    directorProof={...proof,instruction,engine,id:directorDraft.id};$('director-outbound').textContent=JSON.stringify(proof.payload,null,2);
    $('director-destination').textContent='发送至 '+(engine==='openai'?'api.openai.com':'api.anthropic.com')+'，仅用于本次剪辑方案；模型由主机管理员配置。';
    $('director-consent-error').textContent='';$('director-consent-check').checked=false;$('director-cloud-confirm').disabled=true;$('director-consent').showModal();
  }catch(e){directorError(e);}finally{directorLock(false);}
});
$('director-consent-check').addEventListener('change',()=>{$('director-cloud-confirm').disabled=!$('director-consent-check').checked;});
$('director-cloud-confirm').addEventListener('click',async()=>{
  if(!directorProof||!$('director-consent-check').checked)return;
  const proof=directorProof;$('director-cloud-confirm').disabled=true;$('director-consent-check').disabled=true;$('director-consent-error').textContent='正在请求文字编排，最长约 90 秒；不会自动重试…';directorLock(true);
  try{const value=await post('/api/director/drafts/'+proof.id+'/revise',{revision:proof.revision,engine:proof.engine,instruction:proof.instruction,consent:true,digest:proof.digest});$('director-consent').close();directorShow(value);}
  catch(e){$('director-consent-error').textContent=e.message;}
  finally{directorProof=null;$('director-consent-check').disabled=false;$('director-consent-check').checked=false;directorLock(false);}
});
$('director-render').addEventListener('click',async()=>{
  if(directorDirty)return directorError(new Error('请先保存镜头调整，再确认方案'));
  if(directorBusy)return;directorLock(true);
  try{await post('/api/director/drafts/'+directorDraft.id+'/render',{revision:directorDraft.revision});$('director-dialog').close();toast('已按确认的方案开始制作；原片不会改写');await refreshFilms();await pollJobs();}catch(e){directorError(e);}finally{directorLock(false);}
});
$('director-dialog').addEventListener('cancel',event=>{if(directorBusy)event.preventDefault();});
$('director-consent').addEventListener('cancel',event=>{if(directorBusy)event.preventDefault();});
$('director-resume').addEventListener('click',async()=>{
  try{const {items}=await api('/api/director/drafts');$('director-drafts').hidden=false;$('director-drafts').replaceChildren();
    for(const draft of items)$('director-drafts').append(action(draft.title+' · v'+draft.revision,async()=>{try{directorShow(await api('/api/director/drafts/'+draft.id));const status=await api('/api/director/status');$('director-engine').replaceChildren(new Option('本地规则 · 不联网','local'),...status.providers.map(p=>{const option=new Option(p.label+(p.configured?' · 仅文字编排':' · 未配置'),p.id);option.disabled=!p.configured;return option;}));}catch(e){toast(e.message);}},'secondary compact'));
    if(!items.length)$('director-drafts').append(node('p','还没有方案。先在上方预览选片，再进入剪辑工作台。','small'));
  }catch(e){toast(e.message);}
});
async function openDirectorFeedback(film){
  try{const [draft,saved]=await Promise.all([api('/api/director/films/'+film.id+'/plan'),api('/api/director/films/'+film.id+'/feedback')]);directorFeedbackFilm=film;$('director-feedback-form').reset();$('director-feedback-download').hidden=!saved;$('director-feedback-download').href='/api/director/films/'+film.id+'/feedback-report';$('director-feedback-status').textContent=saved?'已读取本机保存的反馈。':'';if(saved){const form=$('director-feedback-form');for(const key of ['story','pace','selection','sound'])form.elements[key].value=saved.scores[key];form.elements.comment.value=saved.comment;form.querySelectorAll('[name=issues]').forEach(input=>input.checked=saved.issues.includes(input.value));}$('director-film-plan').textContent=JSON.stringify(draft.plan,null,2);$('director-feedback').showModal();}catch(e){toast(e.message);}
}
$('director-feedback-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  try{const fields=new FormData(event.target);const scores=Object.fromEntries(['story','pace','selection','sound'].map(key=>[key,Number(fields.get(key))]));await post('/api/director/films/'+directorFeedbackFilm.id+'/feedback',{scores,issues:fields.getAll('issues'),comment:fields.get('comment')});$('director-feedback-status').textContent='已保存在本机；可自行下载报告，检查后再提交给开发者。';$('director-feedback-download').href='/api/director/films/'+directorFeedbackFilm.id+'/feedback-report';$('director-feedback-download').setAttribute('download','memory-director-feedback.json');$('director-feedback-download').hidden=false;}catch(e){$('director-feedback-status').textContent=e.message;}finally{button.disabled=false;}
});
