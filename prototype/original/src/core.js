/* 构造数据与可复算规则。生产权限及并发校验须在服务端实现。 */
const DemoCore = (() => {
  const rules = { id:'V0.1-DEMO', max:[30,30,15,15,10], thresholds:[[2,4,8],[10,100,1000],[1000,10000,100000],[2,4,8]], scores:[[0,10,20,30],[0,10,20,30],[0,5,10,15],[0,5,10,15]] };
  const actors=[{id:'u01',name:'张敏',role:'录入采集'},{id:'u02',name:'李悦',role:'业务复核'},{id:'u03',name:'王磊',role:'同步操作'},{id:'u04',name:'陈晨',role:'规则管理'}];
  const stamp = () => new Date().toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'});
  const grade = n => n>=75?'红色':n>=50?'橙色':n>=25?'黄色':'蓝色';
  const band=(v,i)=>rules.scores[i][rules.thresholds[i].filter(x=>v>=x).length];
  function points(v,i){
    if(v==null)return [0,rules.max[i]];
    const range=Array.isArray(v)?v:[v,v];
    return range.map(x=>i===4?x:band(x,i));
  }
  function linkKey(value,platform){
    let u; try{u=new URL(value)}catch(e){throw Error('请输入完整的 https 网页链接')}
    const hosts={'微博':['weibo.com','www.weibo.com'],'小红书':['www.xiaohongshu.com','xiaohongshu.com','xhslink.com'],'抖音':['www.douyin.com','douyin.com','v.douyin.com'],'快手':['www.kuaishou.com','kuaishou.com'],'B站':['www.bilibili.com','bilibili.com','b23.tv'],'贴吧':['tieba.baidu.com']};
    if(u.protocol!=='https:'||u.username||u.password||u.port||!hosts[platform]?.includes(u.hostname))throw Error('链接须为所选平台已批准域名的 HTTPS 地址；演示不解析短链跳转');
    u.hash='';u.search='';return u.href.replace(/\/$/,'');
  }
  function pairedSnapshots(c,at){
    if(c.snapshots.length<2)return null;
    const [a,b]=c.snapshots.slice(-2),hours=(Date.parse(b.at)-Date.parse(a.at))/3600000;
    if(!Number.isFinite(hours)||hours<23.5||hours>24.5||Date.parse(b.at)>Date.parse(at))return null;
    if([a,b].some(s=>['likes','comments','shares'].some(k=>!Number.isSafeInteger(s[k])||s[k]<0)))return null;
    const delta=b.likes+b.comments+b.shares-a.likes-a.comments-a.shares;
    return delta<0?null:{a,b,hours,rate:delta/hours};
  }
  function metrics(r){
    const end=Date.parse(r.at), d=86400000;
    const unique=[...new Map(r.contents.map(c=>[c.key,c])).values()];
    const inWindow=(c,days)=>Date.parse(c.published)<=end&&Date.parse(c.published)>=end-days*d;
    const n=unique.filter(c=>inWindow(c,7)).length;
    const o=r.unknownOriginal?null:new Set(unique.filter(c=>inWindow(c,30)&&c.original).map(c=>c.material)).size;
    let rate=0, rateMissing=false;
    for(const c of unique){
      const pair=pairedSnapshots(c,r.at);if(!pair){rateMissing=true;continue}
      rate+=pair.rate;
    }
    return [n,rateMissing||!unique.length?null:rate,r.followers,o,r.vKnown?({none:0,like:3,comment:5,share:10}[r.v]):null];
  }
  function evaluate(r){
    const values=metrics(r),ranges=values.map(points),L=ranges.reduce((s,x)=>s+x[0],0),U=ranges.reduce((s,x)=>s+x[1],0);
    const reason=r.binding!=='已确认'||!r.phone||!r.bindingSource?'手机号绑定尚未确认或存在争议':!r.contents.length?'至少需要一条可核验的事件相关内容':r.contents.some(c=>!c.summary||!c.published||!c.snapshots.length)?'内容摘要或证据时间缺失':grade(L)!==grade(U)?'未知指标使分数区间跨档，需补充证据':'';
    return {values,ranges,L,U,grade:grade(L)===grade(U)?grade(L):null,reason,limited:values.some(v=>v==null)};
  }
  function invalidate(r,reason){
    r.history.push({version:r.version,state:r.state,result:r.result?JSON.parse(JSON.stringify(r.result)):null,reason,at:stamp()});
    r.version++;r.state='待重评';r.result=null;r.finalGrade=null;r.reviewer=null;
    for(const t of r.tasks){if(['已同步','待更正','更正待核对'].includes(t.state)){t.state='待更正';t.correctionReceipt=null}else if(!['已作废','更正完成'].includes(t.state))t.state='已冻结'}
  }
  function calculate(r,actor){
    if(actor.role!=='录入采集')throw Error('请切换为录入采集人员执行计算并提交');
    if(r.state==='已确认')throw Error('当前版本已确认，请先新增证据或发起重评');
    const result=evaluate(r);r.result=result;r.submitter=actor.id;r.state=result.reason?'证据不足':'待复核';return result;
  }
  function review(r,actor,action,note,adjusted){
    if(actor.role!=='业务复核')throw Error('请切换为业务复核人员');
    if(actor.id===r.submitter)throw Error('提交人与复核人必须为不同人员');
    if(r.state!=='待复核')throw Error('当前记录尚未进入待复核队列');
    if(!note?.trim())throw Error('请填写复核意见或调整依据');
    if(action==='退回'){r.state='复核退回';r.reviewNote=note;return}
    const result=evaluate(r);if(result.reason)throw Error(result.reason);
    if(adjusted&&!['红色','橙色','黄色','蓝色'].includes(adjusted))throw Error('请选择有效等级');
    r.result=result;r.state='已确认';r.finalGrade=adjusted||result.grade;r.reviewer=actor.id;r.reviewNote=note;r.confirmedAt=stamp();
  }
  function createTask(r,actor){
    if(actor.role!=='同步操作')throw Error('请切换为同步操作人员');
    if(r.state!=='已确认'||evaluate(r).reason)throw Error('仅当前有效且复核通过的结果可发起同步');
    const existing=r.tasks.find(t=>t.version===r.version&&!['已作废','已冻结'].includes(t.state));if(existing)return existing;
    const t={id:'SYNC-'+r.id+'-'+r.version,version:r.version,state:'待同步',grade:r.finalGrade,subject:{userId:r.userId,name:r.name,phone:r.phone,eventId:r.eventId},attempts:[],receipt:null,correctionReceipt:null};r.tasks.unshift(t);return t;
  }
  function receipt(r,t,actor,data){
    if(actor.role!=='同步操作')throw Error('请切换为同步操作人员');
    if(!['待同步','同步失败','待更正'].includes(t.state))throw Error('此任务当前不可登记回执');
    if(!data.no?.trim()||!data.at||!data.note?.trim())throw Error('目标记录号、录入时间和办理说明均为必填');
    if(!Number.isFinite(Date.parse(data.at)))throw Error('目标录入时间格式无效');
    if(t.state==='待更正'){t.correctionReceipt={...data,actor:actor.id};t.state='更正待核对';return}
    if(t.version!==r.version||r.state!=='已确认')throw Error('结果版本已变化，任务已冻结，请办理最新版本');
    t.receipt={...data,actor:actor.id};t.attempts.push({...data,actor:actor.id});t.state='待核对';
  }
  function verify(r,t,actor){
    if(actor.role!=='业务复核')throw Error('请切换为业务复核人员执行核对');
    const corr=t.state==='更正待核对', rec=corr?t.correctionReceipt:t.receipt;
    if(!['待核对','更正待核对'].includes(t.state)||!rec)throw Error('请先登记完整目标回执');
    if(rec.actor===actor.id)throw Error('登记人与核对人必须不同');
    if(!corr&&(r.state!=='已确认'||r.version!==t.version))throw Error('结果已失效，请办理更正');
    t.state=corr?'更正完成':'已同步';t.verifiedBy=actor.id;t.verifiedAt=stamp();
  }
  function seed(){
    const names=['宽带移机多次预约未完成','套餐变更后账单存在疑问','携号转网办理进度争议','家庭宽带晚间速率反馈','退订增值业务后持续计费','营业厅服务体验反馈','异地销户资料补交','流量提醒与实际账单差异','装维上门时间反复调整','合约到期续费解释争议'];
    const users=['周女士','刘先生','陈女士','赵先生','吴女士','孙先生','何女士','郑先生','冯女士','许先生'];
    const configs=[[4,120,20000,4,'comment'],[4,null,20000,4,'none'],[8,1200,null,4,'share'],[2,30,2000,2,'none'],[8,1500,150000,8,'share'],[1,5,350,1,'none'],[3,50,1500,2,'like'],[4,200,30000,4,'none'],[2,15,700,2,'none'],[1,1,80,1,'none']];
    const platforms=['微博','小红书','抖音','快手','B站','贴吧'];
    const rows=configs.map((cfg,i)=>{
      const [n,rate,followers,o,v]=cfg;const platform=platforms[i%6];
      const r={id:'YQ-202610-'+String(i+1).padStart(3,'0'),eventId:'EV-202610-'+String(i+1).padStart(3,'0'),userId:'USER-'+(1001+i),name:users[i],title:names[i],platform,phone:'138****'+String(2601+i),binding:i===3?'存在争议':i===8?'待核实':'已确认',bindingSource:'工单核验记录 GD-202610-'+(2301+i),bindingVersion:1,bindingHistory:[],version:1,state:'待评估',submitter:'u01',reviewer:null,at:'2026-10-29T10:00:00+08:00',followers,v,vKnown:true,d6:'未见充分证据',d6Note:'',contents:[],result:null,finalGrade:null,history:[],tasks:[],reviewNote:''};
      for(let j=0;j<n;j++){
        const b={at:'2026-10-29T09:00:00+08:00',likes:Math.round((rate||1)*24/n),comments:40,shares:10};
        const a={at:'2026-10-28T09:00:00+08:00',likes:0,comments:40,shares:10};
        const domain={'微博':'weibo.com','小红书':'www.xiaohongshu.com','抖音':'www.douyin.com','快手':'www.kuaishou.com','B站':'www.bilibili.com','贴吧':'tieba.baidu.com'}[platform];
        r.contents.push({id:r.id+'-C'+j,key:domain+'/demo/'+i+'/'+j,url:'https://'+domain+'/demo/'+i+'/'+j,platform,summary:j===0?'用户反映'+names[i]+'，希望核实业务处理进展并说明处理时限。':'补充反馈：'+names[i]+'，继续记录沟通与处理经过。',published:'2026-10-'+String(23+j%6).padStart(2,'0')+'T08:00:00+08:00',original:j<o,material:i+'-material-'+j,source:'人工补录',snapshots:rate==null?[b]:[a,b]});
      }
      if([0,2].includes(i)){r.result=evaluate(r);r.state='待复核'}
      if(i===1){r.result=evaluate(r);r.state='证据不足'}
      if([4,5,6,7,9].includes(i)){r.result=evaluate(r);r.state='已确认';r.finalGrade=r.result.grade;r.reviewer='u02';r.reviewNote='已核验关联、快照和计分依据。';r.confirmedAt='2026-10-29 10:15:00'}
      if([4,5,6].includes(i)){const t=createTask(r,actors[2]);if([4,5].includes(i)){receipt(r,t,actors[2],{no:'LT-20261029-'+(110+i),at:'2026-10-29T10:20',note:'已按确认版本手工录入。'});if(i===4)verify(r,t,actors[1])}}
      return r;
    });return {schema:1,rows,logs:[{at:'2026-10-29 10:30:00',actor:'陈晨',action:'载入构造演示数据',object:'10条用户事件记录'}],actorId:'u01',menu:'overview',selected:rows[0].id};
  }
  return {rules,actors,stamp,grade,points,pairedSnapshots,metrics,evaluate,linkKey,invalidate,calculate,review,createTask,receipt,verify,seed};
})();
if(typeof module!=='undefined')module.exports=DemoCore;
