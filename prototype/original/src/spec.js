var body = document.getElementById('specBody');
var initialSpec=body.innerHTML, editing=false, saveTimer=null;
function notifySpec(message){document.getElementById('saveTip').textContent=message}
function cleanSpec(raw){
 const t=document.createElement('template');t.innerHTML=raw;
 const tags=new Set(['DIV','SPAN','P','SECTION','H3','H4','B','STRONG','BR','TABLE','THEAD','TBODY','TR','TH','TD','BUTTON']);
 t.content.querySelectorAll('*').forEach(el=>{if(!tags.has(el.tagName)){el.remove();return}for(const a of [...el.attributes])if(!['class','data-menu','data-label','data-k','data-edit','data-user-edited','data-agent-edited','type','aria-label','data-action'].includes(a.name))el.removeAttribute(a.name)});
 return t.innerHTML;
}
try{const cached=localStorage.getItem(PROTO_KEY);if(cached)body.innerHTML=cleanSpec(cached)}catch(e){notifySpec('本地说明缓存不可用，请导出保留修改')}
function save(){
 try{localStorage.setItem(PROTO_KEY,body.innerHTML);notifySpec('已保存到当前浏览器 · '+new Date().toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'}))}catch(e){notifySpec('保存失败，请立即导出 HTML 保留修改')}
}
function setEdit(on){editing=on;body.classList.toggle('editing',on);body.querySelectorAll('[data-edit]').forEach(el=>{if(on)el.setAttribute('contenteditable','plaintext-only');else el.removeAttribute('contenteditable')});document.getElementById('btnEdit').textContent=on?'完成编辑':'编辑需求说明';save()}
body.addEventListener('input',e=>{const el=e.target.closest('[data-edit]');if(el){el.dataset.userEdited='1';clearTimeout(saveTimer);saveTimer=setTimeout(save,250)}});
body.addEventListener('paste',e=>{if(!e.target.closest('[data-edit]'))return;e.preventDefault();const text=e.clipboardData.getData('text/plain');const selection=window.getSelection();if(!selection.rangeCount)return;const range=selection.getRangeAt(0);range.deleteContents();const n=document.createTextNode(text);range.insertNode(n);range.setStartAfter(n);range.collapse(true);selection.removeAllRanges();selection.addRange(range);e.target.dispatchEvent(new Event('input',{bubbles:true}))});
function showMenuPane(m){body.querySelectorAll('.menu-pane').forEach(p=>p.classList.toggle('hidden',p.dataset.menu!==m));document.body.dataset.activeMenu=m}
window.showMenuPane=showMenuPane;
function setSpecHidden(hidden,persist=true){document.body.dataset.spec=hidden?'hidden':'shown';document.getElementById('specToggle').textContent=hidden?'显示需求说明':'隐藏需求说明';if(persist)try{localStorage.setItem(PROTO_KEY+':hidden',hidden?'1':'0')}catch(e){notifySpec('说明显隐未能保存')}}
window.setSpecHidden=setSpecHidden;
function toggleSpec(){setSpecHidden(document.body.dataset.spec!=='hidden')}
try{const hidden=localStorage.getItem(PROTO_KEY+':hidden');if(hidden!==null)setSpecHidden(hidden==='1',false);else setSpecHidden(document.body.dataset.spec==='hidden'||window.innerWidth<850,false)}catch(e){}
function flashAll(k,opts={}){
 if(document.body.dataset.spec==='hidden')setSpecHidden(false);
 document.querySelectorAll('[data-k]').forEach(el=>{if(el.dataset.k!==k||!el.offsetParent)return;el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash')});
 if(!opts.noScroll){const pane=[...body.querySelectorAll('.menu-pane')].find(p=>!p.classList.contains('hidden'));const item=[...pane.querySelectorAll('.fn')].find(el=>el.dataset.k===k);item?.scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'})}
}
window.flashAll=flashAll;window.flashAllNoScroll=k=>flashAll(k,{noScroll:true});
body.addEventListener('click',e=>{if(e.target.isContentEditable)return;const remove=e.target.closest('[data-action="delete-section"]');if(remove){if(confirm('删除该自定义章节？可在导出副本中保留历史。')){remove.closest('section').remove();save()}return}const item=e.target.closest('.fn');if(item)window.flashAllNoScroll(item.dataset.k)});
function addSection(){const name=prompt('新章节名称','补充业务说明');if(name===null||!name.trim())return;const pane=[...body.querySelectorAll('.menu-pane')].find(p=>!p.classList.contains('hidden'));const section=document.createElement('section');section.className='user-section';section.dataset.userEdited='1';const heading=document.createElement('h3');heading.dataset.edit='';heading.dataset.userEdited='1';heading.textContent=name;const del=document.createElement('button');del.type='button';del.textContent='删除章节';del.dataset.action='delete-section';const text=document.createElement('div');text.className='sub-v';text.dataset.edit='';text.dataset.userEdited='1';text.textContent='在此补充业务口径或评审意见。';section.append(heading,del,text);pane.append(section);if(editing)section.querySelectorAll('[data-edit]').forEach(el=>el.setAttribute('contenteditable','plaintext-only'));save()}
function resetSpec(){if(!confirm('恢复生成时的初始说明？当前说明修改将丢失，建议先导出。业务数据不受影响。'))return;body.innerHTML=initialSpec;showMenuPane(document.body.dataset.activeMenu);setEdit(false);save()}
function fileStamp(){const parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());return parts.replaceAll('-','').replace(' ','-').replaceAll(':','')}
function download(name,text,type){const blob=new Blob([text],{type:type+';charset=utf-8'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.append(a);a.click();setTimeout(()=>{URL.revokeObjectURL(a.href);a.remove()},1000)}
function safeJSON(value){return JSON.stringify(value).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029')}
function makeExportHtml(){
 setEdit(false);
 const root=document.documentElement.cloneNode(true);root.querySelector('#app').innerHTML='';root.querySelector('#app').setAttribute('v-cloak','');
 root.querySelectorAll('[contenteditable]').forEach(el=>el.removeAttribute('contenteditable'));root.querySelector('#specBody').classList.remove('editing');
 root.querySelector('#saveTip').textContent='';root.querySelector('#btnEdit').textContent='编辑需求说明';
 root.querySelectorAll('.flash,.flashbg').forEach(el=>el.classList.remove('flash','flashbg'));
 const newKey='proto-yuqing-'+crypto.randomUUID();root.querySelector('#runtimeKey').textContent='var PROTO_KEY = "'+newKey+'";';
 root.querySelector('#embeddedState').textContent='window.EMBEDDED_STATE = '+safeJSON(window.getDemoState())+';';
 root.querySelector('#specBody').innerHTML=cleanSpec(root.querySelector('#specBody').innerHTML);
 return '<!DOCTYPE html>\n'+root.outerHTML;
}
window.makeExportHtml=makeExportHtml;
function exportHtml(){try{download('外部舆情评估系统-原型-'+fileStamp()+'.html',makeExportHtml(),'text/html');notifySpec('已导出当前业务和说明；请把导出文件交给 Agent 合并')}catch(e){notifySpec('导出失败：'+e.message)}}
showMenuPane(document.body.dataset.activeMenu||'overview');
