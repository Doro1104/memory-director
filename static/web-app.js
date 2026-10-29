'use strict';
let installPrompt,checkingConnection=false;
const standalone=()=>window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
function installContext(){
  document.querySelectorAll('[data-install-help]').forEach(button=>button.textContent=standalone()?'主屏幕使用说明':'添加到主屏幕');
  $('install-context').textContent=(standalone()?'你已经从主屏幕打开了记忆导演。':'添加后可从桌面图标进入。')+(window.isSecureContext?'当前连接支持浏览器安装能力，具体选项由浏览器决定。':'当前是普通局域网 HTTP：传输不加密，请只在可信家庭网络使用。可添加桌面入口，离线提示与自动保持亮屏不可用。');
}
document.querySelectorAll('[data-install-help]').forEach(button=>button.addEventListener('click',()=>{installContext();$('install-dialog').showModal();}));
window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;$('install-native').hidden=false;});
$('install-native').addEventListener('click',async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;installPrompt=null;$('install-native').hidden=true;});
window.addEventListener('appinstalled',()=>{$('install-native').hidden=true;installPrompt=null;installContext();});
function networkStatus(ok){
  $('network-status').hidden=ok;
  $('network-message').textContent='暂时连接不到家里的电脑。请确认电脑开机、记忆导演已启动，手机与电脑连接同一个 Wi-Fi。';
}
async function checkConnection(){
  if(checkingConnection||document.visibilityState==='hidden')return;
  checkingConnection=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),6000);
  try{const response=await fetch('/api/reachability',{cache:'no-store',signal:controller.signal});const result=await response.json();networkStatus(!response.ok?false:result.ok===true);}
  catch{networkStatus(false);}finally{clearTimeout(timer);checkingConnection=false;}
}
$('network-retry').addEventListener('click',async()=>{await checkConnection();if($('network-status').hidden&&!user)await init().catch(e=>{$('auth-error').textContent=e.message;});});
window.addEventListener('online',checkConnection);window.addEventListener('offline',()=>networkStatus(false));
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')checkConnection();});
setInterval(checkConnection,30000);checkConnection();installContext();
if(window.isSecureContext&&'serviceWorker' in navigator){
  // The worker only caches the public connection-help page and its own assets.
  navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'}).catch(()=>{});
}
async function copyConnection(value,button){
  try{await navigator.clipboard.writeText(value);toast('已复制');}
  catch{const field=node('textarea');field.value=value;field.readOnly=true;field.className='connection-copy';button.after(field);field.focus();field.select();field.setSelectionRange(0,value.length);toast('请长按或使用系统复制所选内容');}
}
$('connect-button').addEventListener('click',async()=>{
  $('connect-dialog').showModal();$('connection-list').replaceChildren(node('p','正在寻找家里的连接地址…'));
  try{
    const info=await api('/api/connect');$('connection-list').replaceChildren();
    const urls=info.web_urls||info.urls||[];
    if(!urls.length)$('connection-list').append(node('p','网页局域网连接尚未开启。请在电脑按 README 启用 -EnableWebLan 并重启服务；只在可信家庭网络使用。','small'));
    for(const [index,url]of urls.entries()){
      const block=node('section','','connection');const address=new URL(url);
      const qr=info.web_urls?'/api/qr?mode=web&index='+index:'/api/qr?'+new URLSearchParams({ip:address.hostname});
      const link=node('a',url,'connection-url');link.href=url;link.target='_blank';link.rel='noopener';
      block.append(node('h3','手机浏览器访问'),photo(qr,'手机相机扫描，打开记忆导演网页'),link,action('复制网页地址',event=>copyConnection(url,event.currentTarget),'secondary compact'));
      if(address.protocol==='http:')block.append(node('p','普通 HTTP，传输不加密；仅在可信家庭 Wi-Fi 下使用。','small'));
      $('connection-list').append(block);
    }
    if(info.pairing?.length){
      const details=node('details','','native-pairing');details.append(node('summary','已有原生 iPhone App？展开加密配对'));
      for(const [index,pair]of info.pairing.entries()){
        const block=node('section','','connection');block.append(photo('/api/qr?index='+index,'仅供原生 iPhone App 扫描的加密配对码'),node('p','仅供原生 App，手机相机不能用此码打开网页。','small'),action('复制 App 配对信息',event=>copyConnection(JSON.stringify(pair),event.currentTarget),'secondary compact'));details.append(block);
      }
      $('connection-list').append(details);
    }
  }catch(e){$('connection-list').replaceChildren(node('p','无法获取连接地址：'+e.message));}
});
