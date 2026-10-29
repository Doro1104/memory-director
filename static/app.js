'use strict';
const $ = id => document.getElementById(id);
const titles={mine:['总图库','YOUR COMPLETE ARCHIVE','所有收录的照片与视频，都在这里。'],shared:['家人共享','OUR SHARED MOMENTS','只有主动分享的素材会出现在这里。'],favorites:['珍藏片刻','CLOSE TO YOUR HEART','你亲手选出的喜欢，一份总图库里的专属精选。'],today:['往年今日','ON THIS DAY, BACK THEN','同一个月日，不同年份的生活。'],trash:['回收站','NOT GONE, JUST SET ASIDE','移到这里的素材已从图库与分享中隐藏。原文件仍保留，可随时恢复。']};
let user,csrf,needsSetup,scope='mine',kind='',page=0,person=null,people=[],currentAsset,currentFilm,currentFace;
let loading=false,uploading=false,generation=0,selecting=false,selected=new Set(),visibleAssets=[],jobTimer,queryTimer,toastTimer,peopleLimit=60,allTrash=false,excluded=new Set(),editingName=false,musicTracks=[],reviewSettings,reviewSelected=new Set();
let imageZoom=1,imageFit=1;
const node=(tag,value='',cls='')=>{const el=document.createElement(tag);el.textContent=value;el.className=cls;return el;};
const action=(label,fn,cls='secondary')=>{const el=node('button',label,cls);el.type='button';el.addEventListener('click',fn);return el;};
const photo=(src,alt='')=>{const img=document.createElement('img');img.src=src;img.alt=alt;img.loading='lazy';img.decoding='async';return img;};
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,options={}){
  const res=await fetch(path,{...options,headers:{'X-CSRF-Token':csrf||'',...(typeof options.body==='string'?{'Content-Type':'application/json'}:{}),...options.headers}});
  const data=await res.json();
  if(!res.ok){if(res.status===401&&path!='/api/login'){clearInterval(jobTimer);$('shell').hidden=true;$('auth').hidden=false;}throw new Error(data.error||'操作失败，请重试');}
  return data;
}
const post=(url,body={})=>api(url,{method:'POST',body:JSON.stringify(body)});
const patch=(url,body)=>api(url,{method:'PATCH',body:JSON.stringify(body)});
function dateLabel(date){if(!/^\d{4}/.test(date))return '时间待确认';const [y,m,d]=date.slice(0,10).split('-');return y+' 年'+(m?' '+Number(m)+' 月':'')+(d?' '+Number(d)+' 日':'');}
async function init(){
  const state=await api('/api/session');user=state.user;csrf=state.csrf;needsSetup=state.setup;
  $('auth').hidden=!!user;$('shell').hidden=!user;
  if(!user){$('auth-title').textContent=needsSetup?'开启你的记忆馆':'欢迎回家';$('auth-description').textContent=needsSetup?'在这台电脑上创建第一个家庭账号。':'登录，接着翻看你们的故事。';$('auth-submit').textContent=needsSetup?'创建记忆馆 →':'进入记忆馆 →';return;}
  $('user-name').textContent=user.name;$('avatar').textContent=user.name.slice(0,1);$('admin-settings').hidden=!user.admin;
  await Promise.all([refreshStats(),refreshPeople()]);await navigate('mine');await pollJobs();
  clearInterval(jobTimer);jobTimer=setInterval(()=>pollJobs().catch(()=>{}),5000);
}
async function refreshStats(){
  const stats=await api('/api/stats');$('stat-total').textContent=stats.total.toLocaleString()+' 个片刻';$('stat-videos').textContent=stats.videos.toLocaleString()+' 段视频';$('stat-shared').textContent=stats.shared.toLocaleString()+' 个片刻已分享';
  const old=$('year-filter').value;$('year-filter').replaceChildren(new Option('全部年份',''),...stats.years.map(y=>new Option(y+' 年',y)));$('year-filter').value=stats.years.includes(old)?old:'';
}
async function navigate(next,chosenPerson=null){
  scope=next;person=chosenPerson;generation++;selected.clear();allTrash=false;excluded.clear();selecting=false;selectionUI();
  document.querySelectorAll('[data-scope]').forEach(el=>el.classList.toggle('active',el.dataset.scope===scope));
  const home=scope==='mine'&&!person;
  for(const id of ['home-hero','collections','home-people'])$(id).hidden=!home;
  $('people-panel').hidden=scope!=='people';$('films-panel').hidden=scope!=='family';$('library').hidden=['people','family'].includes(scope);
  $('person-edit').hidden=!person;$('select-mode').hidden=!['mine','trash'].includes(scope);
  $('clear-trash').hidden=scope!=='trash';
  $('search').value='';$('year-filter').value='';kind='';document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',b.dataset.kind===''));
  try{if(scope==='people')await refreshPeople();else if(scope==='family')await refreshFilms();else await loadAssets(true);}catch(e){toast(e.message);}
}
function selectionUI(){
  $('selection-bar').hidden=!selecting;$('selection-count').textContent=allTrash?'已全选回收站（排除 '+excluded.size+' 个）':'已选择 '+selected.size+' 个';$('batch-trash').textContent=scope==='trash'?'恢复所选':'移到回收站';$('batch-trash').disabled=!allTrash&&!selected.size;
  $('select-all-trash').hidden=scope!=='trash';$('purge-selected').hidden=scope!=='trash';$('purge-selected').disabled=!allTrash&&!selected.size;
  $('select-mode').textContent=selecting?'完成':'选择';
  document.querySelectorAll('.asset-card').forEach(el=>{const checked=allTrash?!excluded.has(el.dataset.id):selected.has(el.dataset.id);el.classList.toggle('selectable',selecting);el.classList.toggle('selected',checked);el.setAttribute('aria-pressed',String(checked));});
}
async function loadAssets(reset){
  if(['people','family'].includes(scope))return;
  if(reset){generation++;page=0;visibleAssets=[];$('gallery').replaceChildren();}else if(loading)return;
  const run=generation;loading=true;$('gallery-error').textContent='';$('load-more').hidden=true;$('empty').hidden=true;
  const params=new URLSearchParams({scope,kind,page,q:$('search').value,year:$('year-filter').value});if(person)params.set('person',person.id);
  try{
    const result=await api('/api/assets?'+params);if(run!==generation)return;
    const title=person?[person.name||'未命名人物','A FAMILIAR FACE','这个人物出现在以下照片与视频中。']:titles[scope];
    $('section-title').firstChild.textContent=title[0];$('section-eyebrow').textContent=title[1];$('scope-description').textContent=title[2];$('result-count').textContent=result.total.toLocaleString()+' 个片刻';
    visibleAssets.push(...result.items);
    for(const asset of result.items){
      const month=/^\d{4}-\d{2}/.test(asset.date)?asset.date.slice(0,7):'unknown';let grid=document.querySelector('[data-month="'+month+'"]');
      if(!grid){const heading=node('div','','month-heading');heading.append(node('h3',month==='unknown'?'时间待确认':dateLabel(month)),node('span','LIFE IN FRAMES'));grid=node('div','','photo-grid');grid.dataset.month=month;$('gallery').append(heading,grid);}
      const card=node('button','','asset-card');card.dataset.id=asset.id;card.type='button';
      const img=photo('/media/'+asset.id+'/thumb',asset.name);img.addEventListener('error',()=>img.src='/static/film.svg',{once:true});
      const meta=node('div','','asset-meta');meta.append(node('strong',asset.name),node('small',dateLabel(asset.date)+(scope==='shared'?' · '+asset.owner_name:'')));
      card.append(img,meta,node('span','✓','selection-check'));
      if(asset.kind==='video'||asset.favorite||asset.shared)card.append(node('span',asset.kind==='video'?'▷ 视频':asset.favorite?'♥':'⌂','card-badge'));
      card.addEventListener('click',()=>{if(selecting){if(asset.owner!==user.id)return toast('只能管理自己收录的素材');if(allTrash){excluded.has(asset.id)?excluded.delete(asset.id):excluded.add(asset.id);}else{if(!selected.has(asset.id)&&selected.size>=100)return toast('一次最多选择 100 个素材');selected.has(asset.id)?selected.delete(asset.id):selected.add(asset.id);}selectionUI();}else openAsset(asset);});grid.append(card);
    }
    if(scope==='mine'&&!person&&reset){const cover=result.items.find(a=>a.kind==='image');if(cover){$('hero-photo').src='/media/'+cover.id+'/thumb';$('hero-photo').hidden=false;}}
    $('empty').hidden=result.total>0;$('empty-title').textContent=scope==='trash'?'这里没有暂别的回忆':scope==='favorites'?'把喜欢的片刻，留在这里':scope==='today'?'今天，等待一次重逢':'故事，从一个片刻开始';
    $('empty-text').textContent=scope==='favorites'?'在总图库打开照片或视频，点击「珍藏」，它就会出现在这里。':scope==='shared'?'打开照片选择「分享给家人」，与家人一起回看。':scope==='trash'?'删除的素材会先移到这里，可随时恢复。':scope==='today'?'暂时没有往年这一天的记录。日期待确认的素材不会被猜测归类。':'收录新照片，或在「家庭与存储」接入旧图库。';
    $('empty-upload').hidden=scope!=='mine';$('load-more').hidden=(page+1)*60>=result.total;selectionUI();
  }catch(e){if(run===generation)$('gallery-error').textContent=e.message;}finally{if(run===generation)loading=false;}
}
let playbackRun=0;
async function prepareVideo(asset,media,retry=false){
  const run=++playbackRun;$('retry-video').hidden=true;media.removeAttribute('src');media.load();
  $('viewer-note').textContent='正在准备兼容播放版本；首次打开可能稍等片刻，原视频保持不变。';
  try{
    let state=await post('/api/assets/'+asset.id+'/playback',{retry});
    while(['queued','running'].includes(state.status)){
      if(run!==playbackRun||!media.isConnected)return;
      $('viewer-note').textContent=state.status==='queued'?'视频已排队，正在等待本机处理…':'正在本机准备兼容播放版本，请稍候…';
      await new Promise(resolve=>setTimeout(resolve,800));
      if(run!==playbackRun||!media.isConnected)return;
      state=await api('/api/assets/'+asset.id+'/playback');
    }
    if(run!==playbackRun||!media.isConnected)return;
    if(state.status!=='done')throw new Error(state.error||'视频准备未完成，请重试');
    media.addEventListener('loadeddata',()=>{if(run===playbackRun)$('viewer-note').textContent='兼容播放已就绪 · 原视频未改动，可直接播放和拖动进度。';},{once:true});
    media.addEventListener('error',()=>{if(run===playbackRun){$('viewer-note').textContent='播放加载未完成，请重新准备视频。';$('retry-video').hidden=false;}},{once:true});
    media.src=state.url;media.load();
  }catch(e){if(run===playbackRun&&media.isConnected){$('viewer-note').textContent=e.message;$('retry-video').hidden=false;}}
}
async function openAsset(asset){
  playbackRun++;$('retry-video').hidden=true;
  currentAsset=asset;currentFilm=null;const isImage=asset.kind==='image'&&!asset.deleted_at;const media=asset.deleted_at?photo('/media/'+asset.id+'/thumb',asset.name):asset.kind==='video'?document.createElement('video'):photo('/media/'+asset.id+'/display',asset.name);
  $('viewer').classList.toggle('image-viewer',isImage);$('photo-tools').hidden=!isImage;
  if(asset.kind==='video'&&!asset.deleted_at){media.controls=true;media.playsInline=true;media.preload='auto';media.poster='/media/'+asset.id+'/thumb';}
  $('viewer-media').replaceChildren(media);$('viewer-name').textContent=asset.name;$('viewer-date').textContent=dateLabel(asset.date);$('viewer-source').textContent=asset.date_source+' · '+asset.owner_name+' · '+(asset.bytes/1024/1024).toFixed(1)+' MB';
  $('favorite-button').hidden=!!asset.deleted_at;$('favorite-button').textContent=asset.favorite?'♥ 已珍藏':'♡ 珍藏';$('share-button').hidden=!!asset.deleted_at||asset.owner!==user.id;$('share-button').textContent=asset.shared?'收回家庭分享':'分享给家人';
  $('download-button').hidden=!!asset.deleted_at;$('download-button').href='/media/'+asset.id+'/download';$('download-button').setAttribute('download',asset.name);
  $('delete-button').hidden=asset.owner!==user.id;$('delete-button').textContent=asset.deleted_at?'恢复到图库':'移到回收站';$('asset-faces').replaceChildren();
  $('viewer-note').textContent=asset.deleted_at?'素材处于回收站，原文件仍保留。':isImage?'正在读取高清画面…':'人物分组可点头像纠正；视频无法直接播放时，可下载原片。';
  $('asset-note-form').hidden=!!asset.deleted_at;$('asset-note').value=asset.note||'';$('asset-note').readOnly=asset.owner!==user.id;$('save-note').hidden=asset.owner!==user.id;$('note-status').textContent=asset.owner!==user.id?'仅上传者可修改备注':'';
  if(isImage){media.loading='eager';$('photo-original').href='/media/'+asset.id+'/display';media.addEventListener('load',()=>{if(currentAsset?.id!==asset.id)return;fitPhoto();$('viewer-note').textContent='高清画面 · '+media.naturalWidth+' × '+media.naturalHeight+' · 可放大查看，无需下载副本。';},{once:true});media.addEventListener('error',()=>{$('viewer-note').textContent='高清原图暂时无法读取，请确认素材硬盘已连接。';},{once:true});}
  if(!$('viewer').open)$('viewer').showModal();
  if(asset.kind==='video'&&!asset.deleted_at)prepareVideo(asset,media);
  if(asset.owner===user.id&&!asset.deleted_at){try{const {items}=await api('/api/assets/'+asset.id+'/faces');if(currentAsset?.id!==asset.id)return;for(const face of items){const button=action('',()=>editFace(face),'face-chip');button.append(photo('/media/face/'+face.id),node('span',face.name||'未命名人物'));$('asset-faces').append(button);}}catch(e){toast(e.message);}}
}
function setPhotoZoom(value){
  const image=$('viewer-media').querySelector('img');if(!image||!image.naturalWidth)return;
  imageZoom=Math.max(.05,Math.min(4,value));image.style.width=Math.round(image.naturalWidth*imageZoom)+'px';image.style.height=Math.round(image.naturalHeight*imageZoom)+'px';
  $('photo-zoom-label').textContent=Math.round(imageZoom*100)+'%';
}
function fitPhoto(){
  const image=$('viewer-media').querySelector('img');if(!image||!image.naturalWidth)return;
  const width=$('viewer-media').clientWidth,height=innerHeight*(innerWidth<=700?.58:.72);
  imageFit=Math.min(width/image.naturalWidth,height/image.naturalHeight,1);setPhotoZoom(imageFit);$('viewer-media').scrollTo(0,0);
}
function confirmAction(title,message){
  $('confirm-title').textContent=title;$('confirm-message').textContent=message;$('confirm-dialog').showModal();
  return new Promise(resolve=>{let confirmed=false;const yes=()=>{confirmed=true;$('confirm-dialog').close();};const close=()=>{$('confirm-yes').removeEventListener('click',yes);resolve(confirmed);};$('confirm-yes').addEventListener('click',yes);$('confirm-dialog').addEventListener('close',close,{once:true});});
}
async function deletionChoice(selection,purge){
  const dialog=$('delete-confirm');$('delete-originals').checked=false;$('delete-plan-error').textContent='';$('delete-plan-details').replaceChildren();$('delete-confirm-title').textContent=purge?'永久移除回收站中的素材？':'移到回收站，还是同时删除原文件？';$('delete-confirm-description').textContent=purge?'不勾选原文件删除时，只移除应用中的记录，磁盘文件仍保留。':'默认只移到应用回收站，可以恢复。只有勾选下面的选项才删除磁盘原文件。';
  let plan,version=0,accepted=false;
  const update=async()=>{const run=++version;$('delete-confirm-yes').disabled=true;$('delete-plan-error').textContent='';try{const next=await post('/api/trash/plan',{...selection,mode:purge?'purge':'trash',delete_originals:$('delete-originals').checked});if(run!==version)return;plan=next;$('delete-plan-details').replaceChildren(node('strong','本次涉及 '+plan.count+' 个素材'+($('delete-originals').checked?'，'+plan.links+' 个原文件入口':'')));if($('delete-originals').checked){for(const entry of plan.examples){const block=node('div');block.append(node('span',entry.name));for(const path of entry.paths)block.append(node('code',path));$('delete-plan-details').append(block);}if(plan.outside_links)$('delete-plan-details').append(node('p','另有 '+plan.outside_links+' 个目录外入口保留，不会删除。','small'));}$('delete-confirm-yes').disabled=!plan.count;$('delete-confirm-yes').textContent=$('delete-originals').checked?'永久删除原文件并移除记录':purge?'永久移除这些记录':'移到回收站';}catch(e){$('delete-plan-error').textContent=e.message;}};
  dialog.showModal();$('delete-originals').addEventListener('change',update);
  const result=new Promise(resolve=>{const yes=()=>{accepted=true;dialog.close();};$('delete-confirm-yes').addEventListener('click',yes);dialog.addEventListener('close',()=>{$('delete-confirm-yes').removeEventListener('click',yes);$('delete-originals').removeEventListener('change',update);version++;resolve(accepted?{...selection,delete_originals:$('delete-originals').checked,confirm:plan.confirm}:null);},{once:true});});update();return result;
}
async function trashAssets(ids,restore,purge=false,all=false){
  if(!all&&!ids.length)return;const selection=all?{all:true,excluded:[...excluded]}:{ids};
  let body;
  if(restore){if(!await confirmAction('让回忆回到图库？','恢复后可以重新浏览和珍藏。家庭分享需重新开启。'))return;body={...selection,restore:true};}
  else {body=await deletionChoice(selection,purge);if(!body)return;}
  try{const result=await post(purge?'/api/trash/purge':'/api/trash',body);$('viewer').close();selected.clear();allTrash=false;excluded.clear();selecting=false;await Promise.all([refreshStats(),loadAssets(true),refreshPeople()]);toast(result.errors?.length?'已移除 '+result.removed+' 个，'+result.errors.length+' 个文件未能删除，仍保留待处理':restore?'已恢复到总图库':body.delete_originals?'已删除原文件并移除记录':purge?'已清除所选记录，磁盘原片仍保留':'已移到回收站');}catch(e){toast(e.message);}
}
function personCard(p,compact=false){const card=node('div','',compact?'person-avatar':'person-card');const avatar=action('',()=>navigate('mine',p),'person-open');avatar.append(photo('/media/face/'+p.cover,p.name||'未命名人物'));const label=action(p.name||'未命名人物',()=>{},'person-name-button');label.title='双击修改人物名称';const begin=()=>inlineRename(p,label);label.addEventListener('dblclick',begin);label.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();begin();}});const line=node('div','','person-name-line');line.append(label,action('✎',begin,'rename-person'));card.append(avatar,line,node('small',p.count+' 个片刻'));return card;}
function inlineRename(p,label){
  if(editingName)return;editingName=true;const input=document.createElement('input');input.className='inline-person-name';input.maxLength=40;input.value=p.name;input.placeholder='输入人物名字';input.setAttribute('aria-label','人物名字');label.replaceWith(input);input.focus();input.select();let saving=false;
  const finish=async(save)=>{if(saving)return;saving=true;try{if(save){await patch('/api/people/'+p.id,{name:input.value});p.name=input.value.trim();if(person?.id===p.id)person.name=p.name;}editingName=false;await refreshPeople();}catch(e){toast(e.message);editingName=false;input.replaceWith(label);}};
  input.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();finish(true);}else if(event.key==='Escape'){event.preventDefault();finish(false);}});input.addEventListener('blur',()=>finish(true));
}
async function refreshPeople(){
  if(editingName)return;
  const data=await api('/api/people');people=data.items;$('people-strip').replaceChildren(...people.slice(0,8).map(p=>personCard(p,true)));
  if(!people.length)$('people-strip').append(action('◎ 在本机识别故事里的人',()=>navigate('people'),'people-start'));
  $('people-summary').textContent=data.pending?'还有 '+data.pending.toLocaleString()+' 个素材等待识别。识别在本机后台运行，结果可手动修正。':'人物已整理；新收录的素材会继续在本机识别。';
  renderPeople();
}
function renderPeople(){const query=$('people-search').value.trim();const filtered=people.filter(p=>(p.name||'未命名人物').includes(query));$('people-grid').replaceChildren(...filtered.slice(0,peopleLimit).map(p=>personCard(p)));$('people-empty').hidden=filtered.length>0;if(filtered.length>peopleLimit)$('people-grid').append(action('继续查看人物 ↓',()=>{peopleLimit+=60;renderPeople();},'secondary people-more'));}
function editPerson(){
  if(!person)return;$('person-name').value=person.name;$('person-dialog').showModal();
}
let mergeSelected=new Set(),mergeTarget='',mergeLimit=60,mergeBusy=false;
function openPeopleMerge(seed=null){
  if(people.length<2)return toast('至少需要两个可见的人物分组才能合并');
  if($('person-dialog').open)$('person-dialog').close();
  mergeSelected=new Set(seed?[seed.id]:[]);mergeTarget=seed?.id||'';mergeLimit=60;
  $('merge-people-search').value=seed?.name||'';$('merge-people-error').textContent='';
  const names=new Map();for(const p of people)if(p.name)names.set(p.name,(names.get(p.name)||0)+1);
  $('merge-same-name').replaceChildren(new Option('选择某个姓名的全部分组',''),...[...names].filter(([,count])=>count>1).map(([name,count])=>new Option(name+' · '+count+' 个分组',name)));
  renderPeopleMerge();$('people-merge-dialog').showModal();
}
function mergeSelectionUI(){
  const chosen=people.filter(p=>mergeSelected.has(p.id));mergeSelected=new Set(chosen.map(p=>p.id));
  if(!mergeSelected.has(mergeTarget))mergeTarget=chosen[0]?.id||'';
  $('merge-selection-count').textContent='已勾选 '+chosen.length+' 个分组（单次最多 200 个）';
  $('merge-target').replaceChildren(...chosen.map(p=>new Option((p.name||'未命名人物')+' · '+p.count+' 个片刻',p.id)));
  $('merge-target').value=mergeTarget;$('merge-target').disabled=mergeBusy||!chosen.length;
  const target=chosen.find(p=>p.id===mergeTarget);$('merge-target-photo').hidden=!target;
  if(target)$('merge-target-photo').src='/media/face/'+target.cover;
  $('merge-selected-faces').replaceChildren(...chosen.map(p=>{const button=action('',()=>{mergeSelected.delete(p.id);renderPeopleMerge();},'merge-selected-person');button.disabled=mergeBusy;button.title='取消选择 '+(p.name||'未命名人物')+' · '+p.count+' 个片刻';button.setAttribute('aria-label',button.title);button.append(photo('/media/face/'+p.cover),node('span',p.name||'未命名人物'),node('span','×'));return button;}));
  $('merge-people-submit').disabled=mergeBusy||chosen.length<2;
  $('merge-people-submit').textContent=mergeBusy?'正在合并…':chosen.length<2?'至少勾选两个分组':'将 '+chosen.length+' 个分组合并为「'+(target.name||'未命名人物')+'」';
}
function renderPeopleMerge(){
  const query=$('merge-people-search').value.trim().toLocaleLowerCase();
  const filtered=people.filter(p=>(p.name||'未命名人物').toLocaleLowerCase().includes(query));
  $('merge-people-grid').replaceChildren(...filtered.slice(0,mergeLimit).map(p=>{
    const card=node('label','','merge-person-card');card.classList.toggle('is-selected',mergeSelected.has(p.id));
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=p.id;checkbox.checked=mergeSelected.has(p.id);checkbox.disabled=mergeBusy;checkbox.setAttribute('aria-label','选择 '+(p.name||'未命名人物')+' · '+p.count+' 个片刻');
    checkbox.addEventListener('change',()=>{if(checkbox.checked&&mergeSelected.size>=200){checkbox.checked=false;return toast('单次最多合并 200 个分组');}checkbox.checked?mergeSelected.add(p.id):mergeSelected.delete(p.id);card.classList.toggle('is-selected',checkbox.checked);$('merge-people-error').textContent='';mergeSelectionUI();});
    card.append(checkbox,photo('/media/face/'+p.cover,p.name||'未命名人物'),node('strong',p.name||'未命名人物'),node('small',p.count+' 个片刻'));return card;
  }));
  $('merge-people-empty').hidden=filtered.length>0;$('merge-people-more').hidden=filtered.length<=mergeLimit;mergeSelectionUI();
}
async function submitPeopleMerge(){
  if(mergeBusy||mergeSelected.size<2)return;
  const ids=[...mergeSelected],target=mergeTarget,updatePerson=person&&mergeSelected.has(person.id);
  mergeBusy=true;$('merge-people-error').textContent='';
  $('people-merge-dialog').querySelectorAll('button,input,select').forEach(el=>el.disabled=true);mergeSelectionUI();
  try{
    const result=await post('/api/people/merge',{ids,target});$('people-merge-dialog').close();await refreshPeople();
    if(updatePerson)await navigate('mine',people.find(p=>p.id===result.id)||{id:result.id,name:result.name});
    else if(scope==='mine')await loadAssets(true);
    toast('已将 '+ids.length+' 个分组合并为「'+(result.name||'未命名人物')+'」');
  }catch(e){$('merge-people-error').textContent=e.message;}
  finally{mergeBusy=false;$('people-merge-dialog').querySelectorAll('button,input,select').forEach(el=>el.disabled=false);renderPeopleMerge();}
}
function editFace(face){currentFace=face;$('face-edit-image').src='/media/face/'+face.id;$('face-person').replaceChildren(...people.map(p=>new Option((p.name||'未命名人物')+' · '+p.count+' 个片刻',p.id)),new Option('单独作为一个新人物','new'));$('face-person').value=face.person;$('face-dialog').showModal();}
async function refreshFilms(){
  const [data,suggestions,options]=await Promise.all([api('/api/films'),api('/api/periods'),api('/api/film-options')]);musicTracks=options.music;
  const oldMusic=$('film-music').value;$('film-music').replaceChildren(new Option('随机选择 CC0 轻音乐','random'),...musicTracks.map(t=>new Option(t.label,t.id)),new Option('只保留视频原声','none'));$('film-music').value=musicTracks.length?([...$('film-music').options].some(o=>o.value===oldMusic)?oldMusic:'random'):'none';
  const oldPerson=$('film-person').value;$('film-person').replaceChildren(new Option('不限人物',''),...people.filter(p=>p.name).map(p=>new Option(p.name,p.id)));if(people.some(p=>p.id===oldPerson))$('film-person').value=oldPerson;
  const periods=suggestions.items;const years=[...new Set(periods.map(p=>p.period.slice(0,4)))];
  const old=$('film-period').value;$('film-period').replaceChildren(...years.map(y=>new Option(y+' 年 · 全年',y)),...periods.map(p=>new Option(dateLabel(p.period)+' · '+p.count+' 个素材',p.period)));if([...$('film-period').options].some(o=>o.value===old))$('film-period').value=old;
  $('film-list').replaceChildren();
  for(const film of data.items){
    const card=node('article','','film-card');const cover=film.status==='done'&&!film.blocked?photo('/media/film/'+film.id+'/thumb',film.title):node('div',film.status==='error'?'暂未完成':film.blocked?'素材在回收站':'正在酝酿一段回忆…','film-placeholder');
    const body=node('div','','film-card-body');body.append(node('span',film.period,'eyebrow'),node('h3',film.title),node('p',film.notes||'完成后即可观看与下载。','small'));
    if(film.status==='done'&&!film.blocked)body.append(action('▷ 开始放映',()=>openFilm(film),'primary'));
    else if(film.status==='error')body.append(action('重新制作',()=>createFilm(film.period,film.title),'secondary'));
    if(film.owner===user.id)body.append(action('删除影片',()=>deleteFilm(film),'danger-button delete-film'));
    card.append(cover,body);$('film-list').append(card);
  }
  if(!data.items.length)$('film-list').append(node('p','放映室还没有影片。选一个月份，让零散片刻自动成为一段回忆。','film-empty'));
  $('period-list').replaceChildren(...periods.slice(0,12).map(p=>{const card=action('',()=>{$('film-period').value=p.period;createFilm(p.period);},'period-card');card.append(photo('/media/'+p.cover+'/thumb'),node('span',dateLabel(p.period)),node('small',p.count+' 个片刻 · 按主题选片 →'));return card;}));
}
async function createFilm(period,title=''){
  reviewSettings={...Object.fromEntries(new FormData($('film-form'))),period,title:title||$('film-form').elements.title.value};reviewSelected.clear();$('film-preview-grid').replaceChildren();$('film-preview-count').textContent='';$('render-reviewed-film').disabled=true;$('film-preview-status').textContent='正在本机按主题分析画面、检查清晰度并去除相似片段，请稍候…';$('film-preview').showModal();
  try{const {items,analyzed,report}=await post('/api/films/preview',reviewSettings);$('film-preview-status').textContent='已分析 '+analyzed+' / '+report.pool_total+' 个候选，建议 '+items.length+' 个。排除明显闭眼 '+report.closed+' 张、相似重复 '+report.duplicates+' 张、低质量 '+report.quality+' 个、偏离主题 '+report.theme+' 个、其他活动 '+report.other_events+' 个。'+(report.range.length?'本次故事：'+report.range.join(' — ')+'。':'')+(report.coarse_dates?'部分素材缺少精确拍摄时间，无法确认是否为同一次活动。':'')+' 侧脸、遮挡或过小人脸可能漏检，请预览确认。';for(const item of items){reviewSelected.add(item.id);const card=node('label','','review-card');const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=true;checkbox.addEventListener('change',()=>{checkbox.checked?reviewSelected.add(item.id):reviewSelected.delete(item.id);reviewCount();});card.append(photo('/media/'+item.id+'/thumb'),checkbox,node('strong',item.name),node('small',item.reason));$('film-preview-grid').append(card);}reviewCount();if(items.length<2)$('film-preview-status').textContent+=' 当前不足两份，请换主题或扩大故事范围，不会用无关素材凑数。';}catch(e){$('film-preview-status').textContent=e.message;}
}
function reviewCount(){$('film-preview-count').textContent='已选 '+reviewSelected.size+' 个片刻';$('render-reviewed-film').disabled=reviewSelected.size<2;}
async function deleteFilm(film){if(!await confirmAction('删除这部回忆影片？','会删除影片文件与封面，并取消尚未完成的制作。制作影片用的照片和视频原片不会被删除。'))return;try{await api('/api/films/'+film.id,{method:'DELETE'});if(currentFilm?.id===film.id)$('viewer').close();await refreshFilms();toast('影片已删除，原始素材保留');}catch(e){toast(e.message);}}
function openFilm(film){
  playbackRun++;$('retry-video').hidden=true;
  currentFilm=film;currentAsset=null;$('viewer').classList.remove('image-viewer');$('photo-tools').hidden=true;$('asset-note-form').hidden=true;const video=document.createElement('video');video.src='/media/film/'+film.id+'/view';video.controls=true;video.playsInline=true;video.preload='metadata';$('viewer-media').replaceChildren(video);
  $('viewer-date').textContent=dateLabel(film.period);$('viewer-name').textContent=film.title;$('viewer-source').textContent=film.notes;$('viewer-note').textContent='影片保存在电脑上，也可下载到 U 盘后在兼容的电视上播放。';$('asset-faces').replaceChildren();$('favorite-button').hidden=true;$('delete-button').hidden=film.owner!==user.id;$('delete-button').textContent='删除影片';$('share-button').hidden=film.owner!==user.id;$('share-button').textContent=film.shared?'收回影片分享':'分享影片给家人';$('download-button').hidden=false;$('download-button').href='/media/film/'+film.id+'/download';$('download-button').setAttribute('download',film.title+'.mp4');$('viewer').showModal();
}
let lastJobSignature='',lastLiveSignature='',polling=false;
async function pollJobs(){
  if(!user||polling)return;polling=true;
  try{const {items}=await api('/api/jobs');const relevant=items.filter(j=>['queued','running','paused','error'].includes(j.status));$('jobs-panel').hidden=!relevant.length;$('jobs-list').replaceChildren();
    const names={faces:'人物识别',videos:'历史视频接入',posters:'视频封面与日期',film:'回忆影片制作',live:'Live Photo 静态整理',playback:'视频兼容播放准备'};
    for(const job of relevant.slice(0,5)){const row=node('div','','job-row');const copy=node('div');copy.append(node('strong',names[job.kind]||job.kind),node('small',job.message||'等待本机处理'));const progress=document.createElement('progress');progress.max=Math.max(job.total,1);progress.value=job.done;row.append(copy,progress,node('span',job.status==='running'?'整理中':job.status==='queued'?'等待处理':job.status==='paused'?'已暂停':'未完成','job-status'));
      if(!['film','playback'].includes(job.kind)&&job.status!=='error')row.append(action(job.status==='paused'?'继续':'暂停',async()=>{try{await patch('/api/jobs/'+job.id,{status:job.status==='paused'?'queued':'paused'});await pollJobs();}catch(e){toast(e.message);}},'text-button'));$('jobs-list').append(row);}
    const signature=items.map(j=>j.id+j.status+Math.floor(j.done/100)).join();if(signature!==lastJobSignature){lastJobSignature=signature;await refreshStats();if(scope==='people'||scope==='mine')await refreshPeople();if(scope==='family')await refreshFilms();}
    const liveSignature=items.filter(j=>j.kind==='live').map(j=>j.id+j.status+j.done).join();if(liveSignature!==lastLiveSignature){lastLiveSignature=liveSignature;if(!['family','people'].includes(scope)&&!selecting&&page===0&&!$('viewer').open)await loadAssets(true);}
  }finally{polling=false;}
}
async function startJob(kind){try{await post('/api/jobs/'+kind);toast(kind==='faces'?'已开始本地人物识别，可继续使用相册':'正在后台扫描历史视频');await pollJobs();}catch(e){toast(e.message);}}
document.querySelectorAll('[data-scope]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.scope)));
document.querySelectorAll('[data-jump]').forEach(b=>b.addEventListener('click',async()=>{await navigate(b.dataset.jump);if(b.dataset.video){kind='video';document.querySelectorAll('[data-kind]').forEach(el=>el.classList.toggle('active',el.dataset.kind===kind));await loadAssets(true);}$('library').scrollIntoView({behavior:'smooth'});}));
document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{kind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(el=>el.classList.toggle('active',el===b));loadAssets(true);}));
document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
$('confirm-cancel').addEventListener('click',()=>$('confirm-dialog').close());
$('login-form').addEventListener('submit',async event=>{event.preventDefault();$('auth-error').textContent='';$('auth-submit').disabled=true;try{await post(needsSetup?'/api/setup':'/api/login',Object.fromEntries(new FormData(event.target)));event.target.reset();await init();}catch(e){$('auth-error').textContent=e.message;}finally{$('auth-submit').disabled=false;}});
$('search').addEventListener('input',()=>{clearTimeout(queryTimer);queryTimer=setTimeout(()=>loadAssets(true),300);});$('year-filter').addEventListener('change',()=>loadAssets(true));$('load-more').addEventListener('click',()=>{page++;loadAssets(false);});
$('hero-film').addEventListener('click',()=>navigate('family'));$('all-people').addEventListener('click',()=>navigate('people'));$('scan-faces').addEventListener('click',()=>startJob('faces'));$('import-videos').addEventListener('click',()=>startJob('videos'));$('refresh-jobs').addEventListener('click',()=>pollJobs().catch(e=>toast(e.message)));$('people-search').addEventListener('input',renderPeople);
$('select-mode').addEventListener('click',()=>{selecting=!selecting;selected.clear();allTrash=false;excluded.clear();selectionUI();});$('cancel-selection').addEventListener('click',()=>{selecting=false;selected.clear();allTrash=false;excluded.clear();selectionUI();});$('select-page').addEventListener('click',()=>{allTrash=false;excluded.clear();for(const a of visibleAssets.filter(a=>a.owner===user.id).slice(0,100))selected.add(a.id);selectionUI();});$('batch-trash').addEventListener('click',()=>trashAssets([...selected],scope==='trash',false,allTrash));$('delete-button').addEventListener('click',()=>currentFilm?deleteFilm(currentFilm):trashAssets([currentAsset.id],!!currentAsset.deleted_at));
$('favorite-button').addEventListener('click',async()=>{try{await patch('/api/assets/'+currentAsset.id,{favorite:!currentAsset.favorite});currentAsset.favorite=!currentAsset.favorite;$('favorite-button').textContent=currentAsset.favorite?'♥ 已珍藏':'♡ 珍藏';toast(currentAsset.favorite?'已加入珍藏片刻':'已取消珍藏');await loadAssets(true);}catch(e){toast(e.message);}});
$('share-button').addEventListener('click',async()=>{try{if(currentFilm){await patch('/api/films/'+currentFilm.id,{shared:!currentFilm.shared});currentFilm.shared=!currentFilm.shared;$('share-button').textContent=currentFilm.shared?'收回影片分享':'分享影片给家人';await refreshFilms();}else{await patch('/api/assets/'+currentAsset.id,{shared:!currentAsset.shared});currentAsset.shared=!currentAsset.shared;$('share-button').textContent=currentAsset.shared?'收回家庭分享':'分享给家人';await refreshStats();await loadAssets(true);}toast('分享设置已更新');}catch(e){toast(e.message);}});
$('viewer').addEventListener('close',()=>{playbackRun++;const video=$('viewer-media').querySelector('video');if(video){video.pause();video.removeAttribute('src');video.load();}$('viewer-media').replaceChildren();});
$('person-edit').addEventListener('click',editPerson);$('person-form').addEventListener('submit',async event=>{event.preventDefault();try{await patch('/api/people/'+person.id,{name:$('person-name').value});person.name=$('person-name').value;$('person-dialog').close();await refreshPeople();await loadAssets(true);}catch(e){toast(e.message);}});
$('merge-button').addEventListener('click',()=>openPeopleMerge(person));
$('home-merge-people').addEventListener('click',()=>openPeopleMerge());$('open-merge-people').addEventListener('click',()=>openPeopleMerge());
$('merge-people-search').addEventListener('input',()=>{mergeLimit=60;renderPeopleMerge();});
$('merge-same-name').addEventListener('change',()=>{const name=$('merge-same-name').value;if(!name)return;const matches=people.filter(p=>p.name===name);if(matches.length>200){$('merge-same-name').value='';return toast('同名分组超过 200 个，请分批勾选合并');}mergeSelected=new Set(matches.map(p=>p.id));mergeTarget=matches[0]?.id||'';$('merge-people-search').value=name;mergeLimit=60;$('merge-people-error').textContent='';renderPeopleMerge();});
$('merge-clear').addEventListener('click',()=>{mergeSelected.clear();mergeTarget='';$('merge-same-name').value='';$('merge-people-error').textContent='';renderPeopleMerge();});
$('merge-target').addEventListener('change',()=>{mergeTarget=$('merge-target').value;mergeSelectionUI();});
$('merge-people-more').addEventListener('click',()=>{mergeLimit+=60;renderPeopleMerge();});
$('merge-people-cancel').addEventListener('click',()=>$('people-merge-dialog').close());$('merge-people-submit').addEventListener('click',submitPeopleMerge);
$('people-merge-dialog').addEventListener('cancel',event=>{if(mergeBusy)event.preventDefault();});
async function correctFace(body){try{await patch('/api/faces/'+currentFace.id,body);$('face-dialog').close();await refreshPeople();if(currentAsset)await openAsset(currentAsset);toast('人物分组已调整');}catch(e){toast(e.message);}}
$('face-move').addEventListener('click',()=>correctFace({person:$('face-person').value}));$('face-remove').addEventListener('click',()=>correctFace({remove:true}));
$('film-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button.primary');button.disabled=true;await createFilm($('film-period').value,event.target.elements.title.value);button.disabled=false;});
$('settings-button').addEventListener('click',async()=>{$('settings-dialog').showModal();try{const info=await api('/api/storage');$('storage-info').replaceChildren();for(const [label,key]of [['手机新上传的照片与视频','uploads'],['旧格式迁移目录（默认不启用）','legacy'],['生成的回忆影片','films'],['账号、珍藏、人物与分享记录','database']]){const block=node('div');block.append(node('strong',label),node('code',info[key]));$('storage-info').append(block);}}catch(e){toast(e.message);}});
$('user-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;try{await post('/api/users',Object.fromEntries(new FormData(event.target)));event.target.reset();toast('家庭账号已创建，请将名字和初始密码告诉家人');}catch(e){toast(e.message);}finally{button.disabled=false;}});
$('import-button').addEventListener('click',async()=>{$('import-button').disabled=true;$('import-status').textContent='正在接入历史照片…';try{const r=await post('/api/import');$('import-status').textContent='已接入 '+r.added+' 张，不可用 '+r.missing+' 张。';await refreshStats();await loadAssets(true);}catch(e){$('import-status').textContent=e.message;}finally{$('import-button').disabled=false;}});
$('logout').addEventListener('click',async()=>{if(uploading)return toast('请等待上传完成');try{await post('/api/logout');clearInterval(jobTimer);lastJobSignature='';await init();}catch(e){toast(e.message);}});
$('connect-button').addEventListener('click',async()=>{$('connect-dialog').showModal();$('connection-list').replaceChildren();try{const info=await api('/api/connect');if(!info.pairing.length){$('connection-list').append(node('p','尚未启用安全局域网连接，请用安装脚本的 -EnableLan 选项配置。'));return;}for(const [index,pair]of info.pairing.entries()){const block=node('div','','connection');block.append(photo('/api/qr?index='+index,'iPhone App 配对二维码'),node('p',pair.url),node('p','请使用记忆导演 iPhone App 扫码。配对信息不含密码。','small'));const copy=action('复制配对信息',async()=>{try{await navigator.clipboard.writeText(JSON.stringify(pair));toast('配对信息已复制');}catch(e){toast('当前浏览器不支持复制，请使用 App 扫码');}},'secondary');block.append(copy);$('connection-list').append(block);}}catch(e){toast(e.message);}});
for(const id of ['upload-button','empty-upload'])$(id).addEventListener('click',()=>{if(uploading)return toast('请等待本次上传完成');$('file-input').click();});
function sendFile(file,index,total){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload?'+new URLSearchParams({name:file.name,modified:file.lastModified}));xhr.setRequestHeader('X-CSRF-Token',csrf);xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.upload.addEventListener('progress',event=>{if(event.lengthComputable)$('transfer-progress').value=((index+event.loaded/event.total)/total)*100;});xhr.addEventListener('load',()=>{try{const result=JSON.parse(xhr.responseText);xhr.status>=400?reject(new Error(result.error||'上传失败')):resolve(result);}catch{reject(new Error('连接中断，请重试'));}});xhr.addEventListener('error',()=>reject(new Error('网络中断，请保持 Wi-Fi 连接')));xhr.send(file);});}
$('file-input').addEventListener('change',async event=>{const files=[...event.target.files];if(!files.length||uploading)return;uploading=true;$('upload-button').disabled=true;$('transfer').hidden=false;$('transfer-close').hidden=true;let success=0,duplicates=0,live=0,failed=[];for(let i=0;i<files.length;i++){$('transfer-counter').textContent=(i+1)+' / '+files.length;$('transfer-status').textContent=files[i].name+' · 请保持页面打开';try{const r=await sendFile(files[i],i,files.length);r.ignored_live?live++:r.duplicate?duplicates++:success++;}catch(e){failed.push(files[i].name+'：'+e.message);}}$('transfer-progress').value=100;$('transfer-status').textContent='已收录 '+success+' 个，跳过重复 '+duplicates+' 个，失败 '+failed.length+' 个。'+(live?'已略过 '+live+' 份 Live 伴随短片。':'')+failed.join('；');$('transfer-close').hidden=false;uploading=false;$('upload-button').disabled=false;event.target.value='';try{await refreshStats();await loadAssets(true);await pollJobs();}catch(e){toast(e.message);}});
$('transfer-close').addEventListener('click',()=>$('transfer').hidden=true);window.addEventListener('beforeunload',event=>{if(uploading){event.preventDefault();event.returnValue='';}});

$('delete-confirm-cancel').addEventListener('click',()=>$('delete-confirm').close());
$('clear-trash').addEventListener('click',()=>{excluded.clear();trashAssets([],false,true,true);});
$('select-all-trash').addEventListener('click',()=>{allTrash=true;excluded.clear();selected.clear();selectionUI();});
$('purge-selected').addEventListener('click',()=>trashAssets([...selected],false,true,allTrash));
$('render-reviewed-film').addEventListener('click',async()=>{$('render-reviewed-film').disabled=true;try{await post('/api/films',{...reviewSettings,ids:[...reviewSelected]});$('film-preview').close();toast('已按确认的素材开始制作高清影片');await refreshFilms();await pollJobs();}catch(e){toast(e.message);reviewCount();}});
$('photo-fit').addEventListener('click',fitPhoto);$('photo-actual').addEventListener('click',()=>setPhotoZoom(1));$('photo-in').addEventListener('click',()=>setPhotoZoom(imageZoom*1.5));$('photo-out').addEventListener('click',()=>setPhotoZoom(imageZoom/1.5));
$('retry-video').addEventListener('click',()=>{const media=$('viewer-media').querySelector('video');if(currentAsset&&media)prepareVideo(currentAsset,media,true);});
$('viewer-media').addEventListener('dblclick',()=>{if(!$('viewer').classList.contains('image-viewer'))return;Math.abs(imageZoom-imageFit)<.01?setPhotoZoom(1):fitPhoto();});
window.addEventListener('resize',()=>{if($('viewer').open&&$('viewer').classList.contains('image-viewer'))fitPhoto();});
$('asset-note-form').addEventListener('submit',async event=>{event.preventDefault();if(!currentAsset||currentAsset.owner!==user.id)return;const aid=currentAsset.id,note=$('asset-note').value;$('save-note').disabled=true;try{await patch('/api/assets/'+aid,{note});const item=visibleAssets.find(a=>a.id===aid);if(item)item.note=note;if(currentAsset?.id===aid){currentAsset.note=note;$('note-status').textContent='备注已保存';}}catch(e){toast(e.message);}finally{$('save-note').disabled=false;}});
$('music-upload-button').addEventListener('click',()=>$('music-file-input').click());
$('music-file-input').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;if(file.size>100*1024*1024){event.target.value='';return toast('单首音乐不能超过 100 MB');}const button=$('music-upload-button');button.disabled=true;button.textContent='正在保存音乐…';try{const result=await api('/api/music?'+new URLSearchParams({name:file.name}),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});await refreshFilms();$('film-music').value=result.id;toast(result.duplicate?'这首音乐已在你的本地音乐库中':'音乐已加入你的配乐库，当前已选中');}catch(e){toast(e.message);}finally{button.disabled=false;button.textContent='＋ 上传本地音乐';event.target.value='';}});
$('film-music').addEventListener('change',()=>{$('music-player').pause();$('music-player').hidden=true;$('music-credit').hidden=true;});
$('music-listen').addEventListener('click',()=>{const value=$('film-music').value;const track=value==='random'?musicTracks[Math.floor(Math.random()*musicTracks.length)]:musicTracks.find(t=>t.id===value);if(!track)return toast('请选择一首配乐，或等待本地音乐库安装完成');$('music-player').hidden=false;$('music-player').src='/media/music/'+track.id;$('music-credit').hidden=false;$('music-credit').textContent=track.title+' · '+track.author+' · '+(track.license==='CC0-1.0'?'CC0':'本地上传');$('music-player').play().catch(()=>toast('请点击播放器开始试听'));});

init().catch(e=>{$('auth').hidden=false;$('auth-error').textContent='无法连接记忆馆：'+e.message;});

