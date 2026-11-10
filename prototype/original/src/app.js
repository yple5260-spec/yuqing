const {createApp,ref,reactive,computed,watch,nextTick}=Vue;
const AppIcon={props:['name'],template:`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path :d="paths[name]||paths.file"/></svg>`,data:()=>({paths:{shield:'M12 3 20 6v6c0 4-4 7-8 9-4-2-8-5-8-9V6l8-3Zm-4 9 3 3 5-6',overview:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',link:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',evidence:'M4 4h16v16H4zM8 8h8M8 12h8M8 16h4',review:'M5 3h14v18H5zM9 8h6M8 13l3 3 5-6',sync:'M4 8h14l-4-4M20 16H6l4 4M18 8l2-2M6 16l-2 2',rules:'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6',file:'M6 3h8l4 4v14H6z'}})};
createApp({template:document.getElementById('protoTpl').innerHTML,components:{AppIcon},setup(){
 const C=DemoCore,actors=C.actors,rules=C.rules,platforms=['微博','小红书','抖音','快手','B站','贴吧'],grades=['红色','橙色','黄色','蓝色'];
 const menus=[{id:'overview',label:'事件与态势',icon:'overview',title:'舆情研判总览',desc:'从传播态势到事件处置，让每一项判断都有据可查。'},{id:'links',label:'链接与绑定',icon:'link',title:'链接与用户关联',desc:'登记来源，核实归属，建立可追溯的事件证据关系。'},{id:'evidence',label:'证据与指标',icon:'evidence',title:'证据与传播指标',desc:'对照原始内容与观测快照，保留采集范围及缺失原因。'},{id:'assessment',label:'评估与复核',icon:'review',title:'风险评估与人工复核',desc:'逐项解释分值，处理证据限制，复核后形成有效结论。'},{id:'sync',label:'同步与更正',icon:'sync',title:'手动同步与更正台账',desc:'从已确认结果到目标记录，用回执和核对完成闭环。'},{id:'rules',label:'规则与审计',icon:'rules',title:'规则与操作审计',desc:'查看评审规则、平台边界及关键操作的留痕。'}];
 const dimensions=[{name:'D1 发布频次',caption:'近7天 · 已纳库去重内容'},{name:'D2 互动频率',caption:'双快照 · 每小时新增互动'},{name:'D3 粉丝量',caption:'关联账号最高可见值'},{name:'D4 生产能力',caption:'近30天 · 去重原创内容'},{name:'D5 大 V 互动',caption:'取最高互动类型，不叠加'}];
 const ruleDescriptions=['0–1条：0；2–3条：10；4–7条：20；≥8条：30。','<10次/小时：0；<100：10；<1000：20；≥1000：30。','<1000：0；<1万：5；<10万：10；≥10万：15。','0–1条：0；2–3条：5；4–7条：10；≥8条：15。','未见：0；点赞：3；评论：5；转发：10。大 V 门槛10万粉丝或批准名单。'];
 const vLabels={none:'本次证据未见互动',like:'可核验点赞',comment:'可核验评论',share:'可核验转发'};
 const colors={'红色':'#bf3543','橙色':'#c77829','黄色':'#b49a34','蓝色':'#3f77b8'};
 const toast=reactive({message:'',error:false});let timer;
 function notify(message,error=false){toast.message=message;toast.error=error;clearTimeout(timer);timer=setTimeout(()=>toast.message='',5000)}
 const initial=window.EMBEDDED_STATE||C.seed();let loaded=initial;
 try{const raw=localStorage.getItem(PROTO_KEY+':business');if(raw){const parsed=JSON.parse(raw);if(parsed.schema!==1||!Array.isArray(parsed.rows)||!parsed.rows.length)throw Error('invalid');loaded=parsed}}catch(e){setTimeout(()=>notify('未能读取本地业务缓存，已载入文件内的演示数据。',true),400)}
 const state=reactive(loaded),actor=computed(()=>actors.find(x=>x.id===state.actorId)||actors[0]),cur=computed(()=>state.rows.find(r=>r.id===state.selected)||state.rows[0]);
 const currentMenu=computed(()=>menus.find(m=>m.id===state.menu)||menus[0]);
 const query=reactive({keyword:'',platform:'',status:''}),applied=reactive({...query}),activeFilter=ref('all'),page=ref(1),guide=ref(false),selectedContent=ref(cur.value.contents[0]?.id||'');
 const content=computed(()=>cur.value.contents.find(c=>c.id===selectedContent.value)||cur.value.contents[0]);
 const baseRows=computed(()=>state.rows.filter(r=>(!applied.keyword||(r.title+r.name+r.id+r.userId+r.eventId).includes(applied.keyword))&&(!applied.platform||r.platform===applied.platform)&&(!applied.status||r.state===applied.status)));
 const filteredRows=computed(()=>baseRows.value.filter(r=>{const f=activeFilter.value;return f==='all'||f==='review'&&r.state==='待复核'||f==='blocked'&&(r.binding!=='已确认'||r.state==='证据不足')||f==='sync'&&r.tasks.some(t=>['待同步','待核对','同步失败','待更正','更正待核对'].includes(t.state))||grades.includes(f)&&r.state==='已确认'&&r.finalGrade===f}));
 const pagedRows=computed(()=>filteredRows.value.slice((page.value-1)*5,page.value*5)),confirmedRows=computed(()=>filteredRows.value.filter(r=>r.state==='已确认'));
 const linkCount=computed(()=>filteredRows.value.reduce((n,r)=>n+r.contents.length,0));
 const platformCounts=computed(()=>platforms.map(name=>({name,count:filteredRows.value.flatMap(r=>r.contents).filter(c=>c.platform===name).length})).filter(x=>x.count));
 const paired=computed(()=>filteredRows.value.flatMap(r=>r.contents.map(c=>C.pairedSnapshots(c,r.at))).filter(Boolean));
 const pairedCount=computed(()=>paired.value.length),trend=computed(()=>[0,1].map(i=>paired.value.reduce((s,pair)=>{const p=i?pair.b:pair.a;return s+p.likes+p.comments+p.shares},0)));
 const trendTime=computed(()=>{if(!paired.value.length)return '暂无有效双快照';const dates=paired.value.flatMap(p=>[p.a.at,p.b.at]).sort((a,b)=>Date.parse(a)-Date.parse(b));return fmt(dates[0])+' 至 '+fmt(dates.at(-1))});
 const trendY=n=>100-n/Math.max(...trend.value,1)*72,trendPath=computed(()=>`M45 ${trendY(trend.value[0])} L290 ${trendY(trend.value[1])}`);
 const scopeLabel=computed(()=>({'all':'全部事件','review':'待复核','blocked':'待补证 / 核实','sync':'同步待办'}[activeFilter.value]||activeFilter.value+'已确认'));
 const liveResult=computed(()=>C.evaluate(cur.value));
 const flowSteps=computed(()=>[{label:'关联核实',done:cur.value.binding==='已确认'},{label:'证据纳库',done:cur.value.contents.some(c=>c.summary&&c.snapshots.length)},{label:'规则评估',done:!!cur.value.result},{label:'人工复核',done:cur.value.state==='已确认'},{label:'同步核对',done:cur.value.tasks.some(t=>t.version===cur.value.version&&t.state==='已同步')}]);
 const statuses=['待评估','证据不足','待复核','复核退回','已确认','待重评'];
 function persist(){try{localStorage.setItem(PROTO_KEY+':business',JSON.stringify(state))}catch(e){notify('本地保存失败，请导出 HTML 保留当前操作。浏览器可能禁用了存储或空间不足。',true)}}
 watch(state,persist,{deep:true});watch(()=>state.selected,()=>{selectedContent.value=cur.value.contents[0]?.id||''});
 watch(()=>state.actorId,()=>log('切换演示身份',actor.value.name));
 function log(action,object=cur.value.id){state.logs.push({at:C.stamp(),actor:actor.value.name,action,object});if(state.logs.length>500)state.logs.shift()}
 function run(fn,message){try{fn();log(message);notify(message);return true}catch(e){notify(e.message,true);return false}}
 function switchMenu(id){state.menu=id;document.body.dataset.activeMenu=id;window.showMenuPane(id);window.scrollTo({top:0,behavior:'instant'})}
 function choose(r,menu){state.selected=r.id;switchMenu(menu)}
 function selectCase(i,menu){choose(state.rows[i],menu)}
 function applyQuery(){Object.assign(applied,query);activeFilter.value='all';page.value=1}
 function resetQuery(){Object.assign(query,{keyword:'',platform:'',status:''});applyQuery()}
 function focusFilter(f){activeFilter.value=f;page.value=1}
 function tone(v){return {'红色':'red','橙色':'orange','黄色':'yellow','蓝色':'blue','已确认':'green','已同步':'green','更正完成':'green','待复核':'blue','待核对':'blue','更正待核对':'orange','待更正':'red','证据不足':'yellow','存在争议':'red','待重评':'orange','待核实':'yellow','同步失败':'red'}[v]||''}
 const rangeText=r=>r.L===r.U?String(r.L):r.L+'–'+r.U,scoreText=p=>p[0]===p[1]?String(p[0]):p[0]+'–'+p[1];
 function valueText(v,i){return v==null?'未知 · 待补证':i===4?({0:'未见互动',3:'点赞',5:'评论',10:'转发'}[v]):Math.round(v*100)/100+([' 条',' 次/小时',' 人',' 条'][i]||'')}
 const actorName=id=>actors.find(a=>a.id===id)?.name||'尚未办理';
 const fmt=x=>x?x.replace('T',' ').replace(/\+08:00$/,'').slice(0,16):'—';
 function syncState(r){return r.tasks.find(t=>['待更正','更正待核对'].includes(t.state))?.state||r.tasks.find(t=>t.version===r.version&&!['已冻结','已作废'].includes(t.state))?.state||'未发起'}
 const focusNum=k=>window.flashAll(k);
 function calculate(){run(()=>C.calculate(cur.value,actor.value),'完成规则计算与提交')}
 function reassess(){if(actor.value.role!=='录入采集')return notify('请切换为录入采集人员发起重评',true);if(!confirm('发起新版本重评？旧版未执行任务将冻结，已同步任务进入更正队列。'))return;run(()=>C.invalidate(cur.value,'人工发起重评'),'已创建待重评版本')}
 function summary(r){if(r.state!=='已确认')return '当前尚无有效的已确认结果。请先完成绑定核实、证据补充与人工复核，再生成同步摘要。';return `评估编号：${r.id}；用户标识：${r.userId}；手机号：${r.phone}；\n舆情事件：${r.eventId} / ${r.title}；\n最终等级：${r.finalGrade}；分数：${rangeText(r.result)}；\n依据：${C.metrics(r).map((v,i)=>dimensions[i].name+' '+valueText(v,i)).join('；')}；\n证据限制：${r.result.limited?'存在缺失项，区间仍在同一等级；':'指标完整；'}仅限当前事件已纳库证据；\n评估时点：${fmt(r.at)}；复核人：${actorName(r.reviewer)}；\n结果版本：v${r.version}；规则：${rules.id}（待审批演示）；\n来源记录：${r.contents.map(c=>c.id).join('、')}。`}
 function requireConfirmed(){if(cur.value.state!=='已确认')throw Error('仅已确认的当前有效版本可复制或导出摘要')}
 async function copySummary(){try{requireConfirmed();await navigator.clipboard.writeText(summary(cur.value));log('复制摘要（同步状态不变）');notify('摘要已复制；同步状态未改变')}catch(e){notify(e.message||'复制失败，请使用导出摘要',true)}}
 function downloadSummary(){run(()=>{requireConfirmed();download(cur.value.id+'-摘要-'+fileStamp()+'.txt',summary(cur.value),'text/plain')},'已导出摘要（同步状态不变）')}
 function generateTask(){run(()=>C.createTask(cur.value,actor.value),'同步任务已准备；重复操作复用原任务')}
 function voidTask(t){if(actor.value.role!=='同步操作')return notify('请切换为同步操作人员',true);if(!confirm('作废这条尚未执行完成的同步任务？'))return;run(()=>{if(!['待同步','同步失败'].includes(t.state))throw Error('当前状态不可作废');t.state='已作废'},'同步任务已作废')}
 const modal=ref(''),form=reactive({}),modalError=ref(''),activeTask=ref(null),batchResults=ref([]),dialog=ref(null);let previousFocus;
 const modalTitle=computed(()=>({intake:'录入事件与链接',binding:'核实 / 更正用户绑定',unbind:'解除错误绑定',batch:'批量导入链接',snapshot:'补充证据快照',metrics:'补充账号与互动核验',review:'人工复核办理',receipt:activeTask.value?.state==='待更正'?'登记目标更正回执':'登记手动录入回执',verify:'核对目标记录',failure:'登记同步失败'}[modal.value]));
 function open(type,t=null){
   previousFocus=document.activeElement;modal.value=type;modalError.value='';batchResults.value=[];activeTask.value=t;Object.keys(form).forEach(k=>delete form[k]);
   const c=content.value;Object.assign(form,{eventId:'EV-202610-011',name:'',title:'',platform:cur.value.platform,phone:cur.value.phone||'',url:'',source:cur.value.bindingSource||'',note:'',binding:cur.value.binding,action:'通过',grade:liveResult.value.grade||'黄色',no:'',at:'2026-10-29T09:00',checked:false,batch:'',contentId:c?.id,summary:c?.summary||'',published:c?.published.slice(0,16)||'2026-10-28T08:00',likes:c?.snapshots.at(-1)?.likes||0,comments:c?.snapshots.at(-1)?.comments||0,shares:c?.snapshots.at(-1)?.shares||0,original:c?.original??true,followers:cur.value.followers??'',v:cur.value.vKnown?cur.value.v:'unknown',d6:cur.value.d6});
   if(type==='intake')form.phone='';
   nextTick(()=>dialog.value?.querySelector('input,select,textarea,button')?.focus());
 }
 function closeModal(){modal.value='';nextTick(()=>previousFocus?.focus())}
 function trap(e){if(e.key!=='Tab'||!modal.value)return;const xs=[...dialog.value.querySelectorAll('button,input,select,textarea')].filter(x=>!x.disabled&&x.offsetParent);const first=xs[0],last=xs.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}
 document.addEventListener('keydown',trap);
 function masked(phone,optional=false){phone=(phone||'').trim();if(!phone&&optional)return '';if(!/^1\d{2}(\d{4}|\*{4})\d{4}$/.test(phone))throw Error('手机号请填写11位号码或 138****1234 格式的构造脱敏值');return phone.slice(0,3)+'****'+phone.slice(-4)}
 function needRole(role){if(actor.value.role!==role)throw Error('请切换为'+role+'人员办理此操作')}
 function intake(data){
   needRole('录入采集');for(const k of ['eventId','name','title','platform','url','source'])if(!String(data[k]||'').trim())throw Error('事件、用户、摘要、平台、链接和来源均需填写');
   const key=C.linkKey(data.url,data.platform),phone=masked(data.phone,true);
   const existing=state.rows.find(r=>r.contents.some(c=>c.key===key||C.linkKey(c.url,c.platform)===key));
   if(existing){if(existing.phone&&phone&&existing.phone!==phone)throw Error('链接已关联其他手机号，需对已有记录进行冲突核验：'+existing.id);throw Error('重复链接，已纳库：'+existing.id)}
   const r={id:'YQ-'+Date.now()+'-'+state.rows.length,eventId:data.eventId.trim(),userId:'DEMO-USER-'+(1001+state.rows.length),name:data.name.trim(),title:data.title.trim(),platform:data.platform,phone,binding:'待核实',bindingSource:data.source.trim(),bindingVersion:1,bindingHistory:[],version:1,state:'待评估',submitter:actor.value.id,reviewer:null,at:'2026-10-29T10:00:00+08:00',followers:null,v:'none',vKnown:false,d6:'待核实',d6Note:'',contents:[{id:'C-'+Date.now()+'-'+state.rows.length,key,url:data.url.trim(),platform:data.platform,summary:'',published:'',original:false,material:key,source:'人工录入',snapshots:[]}],result:null,finalGrade:null,history:[],tasks:[],reviewNote:''};state.rows.unshift(r);state.selected=r.id;return r;
 }
 function fillBatch(){form.batch='EV-202610-011\t杨女士\t宽带预约进度反馈\t微博\thttps://weibo.com/demo/new/101\t138****4210\t工单GD-101\nEV-202610-012\t唐先生\t账单费用核验\t抖音\thttps://www.douyin.com/demo/new/102\t139****4211\t工单GD-102\nEV-202610-013\t钱女士\t链接异常行\t微博\thttp://127.0.0.1/private\t138****4212\t工单GD-103'}
 function submitModal(){
   try{
     const r=cur.value,type=modal.value;
     if(type==='intake'){intake(form);switchMenu('links')}
     if(type==='batch'){
       const lines=form.batch.trim().split(/\r?\n/);if(lines.length>1000)throw Error('单次最多1000行');batchResults.value=lines.map((line,i)=>{try{const vals=line.split('\t');if(vals.length!==7)throw Error('应为7列制表符分隔数据');const keys=['eventId','name','title','platform','url','phone','source'];intake(Object.fromEntries(keys.map((k,j)=>[k,vals[j]])));return {row:i+1,message:'成功'}}catch(e){return {row:i+1,message:e.message}}});log('批量导入逐行处理',batchResults.value.filter(x=>x.message==='成功').length+'行成功 / '+lines.length+'行');notify('批量处理完成，请查看逐行结果');return;
     }
     if(type==='binding'||type==='unbind'){
       needRole('业务复核');if(!form.note.trim())throw Error('请填写核验或更正依据');const phone=type==='unbind'?'':masked(form.phone);if(type==='binding'&&!form.source.trim())throw Error('请填写来源凭据与核验方式');
       const before=r.binding;r.bindingHistory.push({at:C.stamp(),actor:actor.value.name,before,after:type==='unbind'?'已解除':form.binding,oldPhone:r.phone,reason:form.note});C.invalidate(r,form.note);r.binding=type==='unbind'?'已解除':form.binding;r.phone=phone;r.bindingSource=form.source;r.bindingVersion++;
     }
     if(type==='snapshot'){
       needRole('录入采集');const c=r.contents.find(c=>c.id===form.contentId);if(!c)throw Error('请先录入内容链接');
       if(!form.note.trim()||!form.summary.trim())throw Error('请填写内容摘要及快照来源依据');
       const at=form.at+'+08:00',published=form.published+'+08:00';
       if(!Number.isFinite(Date.parse(at))||!Number.isFinite(Date.parse(published))||Date.parse(at)>Date.parse(r.at)||Date.parse(published)>Date.parse(at))throw Error('发布时间须不晚于观测时间，观测时间须不晚于评估基准');
       if(c.snapshots.length&&Date.parse(at)<=Date.parse(c.snapshots.at(-1).at))throw Error('新快照观测时间必须晚于上一快照；请选择需补证的内容');
       for(const k of ['likes','comments','shares'])if(!Number.isSafeInteger(form[k])||form[k]<0)throw Error('互动指标必须为非负安全整数');
       C.invalidate(r,'追加证据快照：'+form.note);c.summary=form.summary;c.published=published;c.original=form.original;c.snapshots.push({at,likes:form.likes,comments:form.comments,shares:form.shares,note:form.note});selectedContent.value=c.id;
     }
     if(type==='metrics'){
       needRole('录入采集');if(!form.note.trim())throw Error('请填写核验依据');const f=form.followers===''?null:Number(form.followers);if(f!==null&&(!Number.isSafeInteger(f)||f<0))throw Error('粉丝量须为非负整数或留空标记未知');C.invalidate(r,'账号核验补充：'+form.note);r.followers=f;r.vKnown=form.v!=='unknown';r.v=form.v==='unknown'?'none':form.v;r.d6=form.d6;r.d6Note=form.note;
     }
     if(type==='review')C.review(r,actor.value,form.action,form.note,form.action==='调整后通过'?form.grade:null);
     if(type==='receipt')C.receipt(r,activeTask.value,actor.value,{no:form.no,at:form.at,note:form.note});
     if(type==='verify'){if(!form.checked)throw Error('请完成核对后勾选确认');C.verify(r,activeTask.value,actor.value)}
     if(type==='failure'){needRole('同步操作');if(!form.note.trim())throw Error('请填写失败原因');if(activeTask.value.state!=='待同步')throw Error('任务状态已变化');activeTask.value.attempts.push({at:C.stamp(),actor:actor.value.id,note:form.note,failed:true});activeTask.value.state='同步失败'}
     log(modalTitle.value);notify('已保存；状态与版本已更新');closeModal();
   }catch(e){modalError.value=e.message}
 }
 function resetDemo(){if(actor.value.role!=='规则管理')return notify('请切换为规则管理人员',true);if(!confirm('重置全部构造业务数据？需求说明编辑不会清除。'))return;Object.assign(state,C.seed());switchMenu('overview');notify('演示业务数据已重置，需求说明保持不变')}
 window.getDemoState=()=>JSON.parse(JSON.stringify(state));
 switchMenu(state.menu||'overview');
 return {state,actor,actors,menus,currentMenu,platforms,grades,colors,query,applied,activeFilter,baseRows,filteredRows,pagedRows,confirmedRows,linkCount,platformCounts,pairedCount,trend,trendTime,trendY,trendPath,scopeLabel,page,guide,cur,content,selectedContent,liveResult,flowSteps,statuses,dimensions,rules,ruleDescriptions,vLabels,toast,modal,form,modalError,modalTitle,activeTask,batchResults,dialog,notify,switchMenu,choose,selectCase,applyQuery,resetQuery,focusFilter,tone,rangeText,scoreText,valueText,actorName,fmt,syncState,focusNum,calculate,reassess,summary,copySummary,downloadSummary,generateTask,voidTask,open,closeModal,submitModal,fillBatch,resetDemo};
}}).mount('#app');
