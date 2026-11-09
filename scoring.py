"""Deterministic, versioned and explainable external propagation scoring."""
import copy, math
DIMS = [
 ('posts7', '近7天相关去重发布数', [2,4,8], [0,10,20,30]),
 ('rate', '每小时新增互动量', [10,100,1000], [0,10,20,30]),
 ('fans', '已核实账号最高可见粉丝量', [1000,10000,100000], [0,5,10,15]),
 ('original30', '近30天已核实原创内容数', [2,4,8], [0,5,10,15]),
 ('bigv', '已核实大V互动', [], [0,3,5,10]),
]
BIGV = ['none', 'like', 'comment', 'repost']
DEFAULT = {'version':1, 'levels':[25,50,75], 'dims':{k:{'thresholds':t,'points':p} for k,_,t,p in DIMS}}
class Invalid(ValueError): pass

def number(v):
 return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def validate_rules(r):
 if not isinstance(r,dict): raise Invalid('规则格式错误')
 levels=r.get('levels',[])
 if len(levels)!=3 or any(not number(x) or not 0<x<=100 for x in levels) or levels!=sorted(set(levels)): raise Invalid('等级阈值必须为递增的三个数，范围1–100')
 total=0
 for k,_,_,_ in DIMS:
  d=r.get('dims',{}).get(k,{})
  p=d.get('points',[]); t=d.get('thresholds',[])
  if len(p)!=4 or any(not number(x) or x<0 for x in p) or p!=sorted(p) or p[0]!=0: raise Invalid('各维度分值须从0起递增且为四档')
  if k!='bigv' and (len(t)!=3 or any(not number(x) or x<0 for x in t) or t!=sorted(set(t))): raise Invalid('各指标阈值须严格递增')
  total+=p[-1]
 if total!=100: raise Invalid('五个维度最高分之和必须为100；维权特征不计分')
 return {'levels':levels,'dims':copy.deepcopy(r['dims'])}

def level(score,r): return ['蓝色','黄色','橙色','红色'][sum(score>=x for x in r['levels'])]

def score(c,r,evidence):
 parts=[]; lo=hi=0; missing=[]; issues=[]
 metrics=c.get('metrics',{})
 for k,title,_,_ in DIMS:
  item=metrics.get(k,{}) or {}; val=item.get('value'); d=r['dims'][k]
  # Every known metric requires its own evidence reference and observation/basis.
  refs=item.get('evidence',[])
  known=val is not None and refs and all(s in evidence for s in refs) and item.get('basis','').strip() and item.get('observed_at')
  if k=='rate' and known:
   pair=item.get('pair') or {}; ids=pair.get('ids',[])
   try:
    from datetime import datetime
    if len(ids)!=2 or ids[0]==ids[1] or not all(x in evidence for x in ids): raise ValueError()
    a,b=[evidence[x] for x in ids]
    if a['source_url']!=b['source_url']: raise ValueError()
    dt=(datetime.fromisoformat(b['captured_at'])-datetime.fromisoformat(a['captured_at'])).total_seconds()/3600
    counts=pair.get('totals',[])
    if dt<=0 or len(counts)!=2 or any(not number(x) or x<0 for x in counts) or counts[1]<counts[0]: raise ValueError()
    derived=(counts[1]-counts[0])/dt
    if abs(derived-val)>0.01: raise ValueError()
   except (ValueError,TypeError,KeyError,OverflowError): known=False; issues.append('互动速率缺少有效同链接、先后双快照及累计数；不得用累计点赞量替代')
  if known:
   idx=BIGV.index(val) if k=='bigv' else sum(val>=t for t in d['thresholds'])
   low=high=d['points'][idx]; why=item['basis']
  else:
   low=0;high=d['points'][-1]; missing.append(title); why='未知/证据不足，不按0分定级'
  lo+=low;hi+=high
  parts.append({'key':k,'title':title,'value':val,'lo':low,'hi':high,'known':bool(known),'why':why,'evidence':refs})
 gate=[]
 if c.get('binding')!='verified' or not c.get('phone') or not c.get('phone_source') or not c.get('binding_basis'): gate.append('手机号绑定待核实')
 if not c.get('binding_verified_by'): gate.append('缺少绑定核验人')
 if not evidence: gate.append('尚无快照证据')
 if not any(p['known'] for p in parts): gate.append('全部指标待采集')
 if not c.get('relevance_basis'): gate.append('缺少本事件相关性说明')
 proposed=level(lo,r) if level(lo,r)==level(hi,r) else None
 return {'lo':lo,'hi':hi,'parts':parts,'missing':missing,'issues':issues,'suggested':proposed,'gates':gate,'eligible':not gate and bool(proposed),'rule_version':r['version']}
