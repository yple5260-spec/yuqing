#!/usr/bin/env python3
"""换芯计划 / 单节点可部署试点版。无浏览器存储依赖；业务规则由服务端执行。"""
import database
import routing
from html import escape
import argparse, base64, csv, hashlib, hmac, io, json, os, re, secrets, sqlite3, threading, time, zipfile
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from xml.etree import ElementTree as ET
from cryptography.fernet import Fernet
from PIL import Image
from scoring import DIMS, BIGV, DEFAULT, Invalid, number, validate_rules, score
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('XIN_DATA_DIR',ROOT/'data')).resolve()
MAX_BODY=20*1024*1024
ROLES={'admin','analyst','reviewer','exporter','viewer'}
LOCK=threading.RLock()

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def js(o): return json.dumps(o,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(b): return hashlib.sha256(b).hexdigest()
def pw_hash(p,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),bytes.fromhex(salt),300000).hex()
def pw_ok(p,hashed): return hmac.compare_digest(pw_hash(p,hashed.split(':')[0]),hashed)
def connect(): return database.connect(DATA)

def enc(o): return CIPHER.encrypt(js(o).encode()).decode()
def dec(s): return json.loads(CIPHER.decrypt(s.encode()))
def url_valid(u):
 try:
  p=urlsplit(u)
  return p.scheme in ('http','https') and bool(p.hostname) and not p.username and not p.password and len(u)<=3000
 except ValueError:return False

def audit(db,who,action,target='',detail=''):
 prev=db.execute('SELECT hash FROM audit ORDER BY id DESC LIMIT 1').fetchone(); prev=prev[0] if prev else '0'*64
 stamp=now(); fields=[stamp,who,action,str(target),str(detail),prev]; sha=digest(js(fields).encode())
 db.execute('INSERT INTO audit(time,actor,action,target,detail,prev,hash) VALUES(?,?,?,?,?,?,?)',(*fields,sha))

def read_rules(db):
 row=db.execute('SELECT version,body FROM rules ORDER BY version DESC LIMIT 1').fetchone()
 return dict(json.loads(row['body']),version=row['version'])
def evidence_for(db,cid): return {r['id']:dict(r) for r in db.execute('SELECT id,source_url,captured_at,sha256,filename,created_at,method,source_cell,note FROM evidence WHERE case_id=? ORDER BY created_at',(cid,))}
def loadcase(db,cid):
 r=db.execute('SELECT * FROM cases WHERE id=?',(cid,)).fetchone()
 if not r: raise Invalid('档案不存在')
 return dict(dec(r['body']),id=r['id'],version=r['version'],created_by=r['created_by'],updated_at=r['updated_at'])
def export_stale(db,row):
 if not row['purpose'].startswith('正式客服导入：'):return False
 raw=CIPHER.decrypt(row['body'].encode()).decode('utf-8-sig');r=read_rules(db)
 for x in csv.DictReader(io.StringIO(raw)):
  try:
   c=loadcase(db,x['档案编号'])
   if c['version']!=int(x['档案版本']) or 'R'+str(r['version'])!=x['规则版本'] or not current_review(db,c,r):return True
  except (Invalid,ValueError,KeyError):return True
 return False

def current_review(db,c,r):
 row=db.execute('SELECT * FROM reviews WHERE case_id=? AND record_version=? AND rule_version=? ORDER BY id DESC LIMIT 1',(c['id'],c['version'],r['version'])).fetchone()
 return dict(row) if row else None

def case_view(db,c,actor,detail=False):
 r=read_rules(db); ev=evidence_for(db,c['id']); s=score(c,r,ev); review=current_review(db,c,r)
 full='admin' in actor['roles'] or 'analyst' in actor['roles']
 phone=c.get('phone',''); c=dict(c); c['phone']=phone if full else (phone[:3]+'****'+phone[-4:] if phone else '')
 c['phone_masked']=phone[:3]+'****'+phone[-4:] if phone else '待绑定'
 c['evaluation']=s;c['review']=review;c['evidence_count']=len(ev)
 c['status']='已确认' if review else ('待复核' if s['eligible'] else ('待绑定' if c.get('binding')!='verified' else '证据待补全'))
 if detail:
  c['evidence']=list(ev.values())
  c['reviews']=[dict(x) for x in db.execute('SELECT * FROM reviews WHERE case_id=? ORDER BY id DESC',(c['id'],))]
 else:c.pop('metrics',None)
 return c

def normalize_case(d,actor,old=None):
 allowed=['event_code','title','platform','url','phone','phone_source','binding','binding_basis','relevance_basis','internal_level','rights_feature','rights_basis','quality_note','source_cell','published_at','metrics']
 c={k:d.get(k,old.get(k,'') if old else '') for k in allowed}
 for k in allowed:
  if k!='metrics':
   if not isinstance(c[k],str) or len(c[k])>5000: raise Invalid('文本字段格式错误或过长')
   c[k]=c[k].strip()
 if not c['title'] or not c['event_code']:raise Invalid('事件编号、事件标题为必填')
 if not url_valid(c['url']): raise Invalid('请输入有效HTTP(S)原文链接')
 if c['phone'] and not re.fullmatch(r'1\d{10}',c['phone']):raise Invalid('手机号须为11位；未知可留空')
 if c['binding'] not in ('pending','verified'):raise Invalid('绑定状态只能为待核实或已核实')
 changed=not old or any(c.get(k)!=old.get(k) for k in ('phone','phone_source','binding_basis','binding'))
 if c['binding']=='verified':
  if not all(c[k] for k in ('phone','phone_source','binding_basis')):raise Invalid('核实绑定必须提供手机号、合规来源和核验依据')
  c['binding_verified_by']=actor['username'] if changed else old.get('binding_verified_by','')
 else:c['binding_verified_by']=''
 if c['rights_feature'] not in ('','unknown','observed','not_observed'):raise Invalid('维权特征值无效')
 m=c['metrics']; c['metrics']={}
 if not isinstance(m,dict):raise Invalid('指标集合须为对象格式')
 for k,_,_,_ in DIMS:
  item=m.get(k,{}) or {}
  if not isinstance(item,dict):raise Invalid('各指标须为对象格式')
  v=item.get('value')
  if v is not None:
   if k=='bigv':
    if v not in BIGV:raise Invalid('大V互动选项无效')
   elif not number(v) or v<0 or v>1e12:raise Invalid('指标必须为非负有限数；未知请留空')
   elif k!='rate' and int(v)!=v:raise Invalid('数量指标须为整数')
  refs=item.get('evidence',[])
  if not isinstance(refs,list) or any(not isinstance(x,str) for x in refs) or len(refs)>50:raise Invalid('指标证据引用格式错误')
  basis=item.get('basis',''); obs=item.get('observed_at','')
  if not isinstance(basis,str) or len(basis)>5000 or not isinstance(obs,str):raise Invalid('指标证据说明格式错误')
  if obs:
   try:
    dt=datetime.fromisoformat(obs)
    if not dt.tzinfo or dt.timestamp()>time.time()+300:raise ValueError()
   except ValueError:raise Invalid('指标观测时间不能晚于当前时间且必须包含时区，如2026-11-06T08:00:00+08:00')
  pair=item.get('pair',{})
  if not isinstance(pair,dict):raise Invalid('双快照格式错误')
  c['metrics'][k]={'value':v,'evidence':refs,'basis':basis.strip(),'observed_at':obs,'pair':pair}
 return c

def save_case(db,c,actor,old=None):
 # Original links are not identity proof. Cross-phone reuse is a hard conflict.
 for row in db.execute('SELECT id,body FROM cases'):
  if old and row['id']==old['id']:continue
  x=dec(row['body'])
  if x['url']==c['url']:raise Invalid('该链接已有档案，请在原档案补充，避免重复绑定')
  if c['phone'] and x.get('phone')==c['phone'] and x['event_code']==c['event_code']:raise Invalid('相同手机号及事件编号已建档')
 if old:
  version=old['version']+1;cid=old['id']
  db.execute('INSERT INTO history(case_id,version,body,actor,time) VALUES(?,?,?,?,?)',(cid,old['version'],enc(old),actor['username'],now()))
  db.execute('UPDATE cases SET body=?,version=?,updated_at=? WHERE id=?',(enc(c),version,now(),cid))
 else:
  cid='HX-'+secrets.token_hex(5).upper();version=1
  db.execute('INSERT INTO cases(id,body,version,created_by,updated_at) VALUES(?,?,?,?,?)',(cid,enc(c),version,actor['username'],now()))
 audit(db,actor['username'],'更新档案' if old else '创建档案',cid,'记录版本 '+str(version)+'；历史复核不删除，新版本需重新复核')
 return cid

def acquire_instance_lock():
 global INSTANCE_LOCK, DATABASE_LOCK
 if database.is_postgres():
  DATABASE_LOCK=database.psycopg.connect(os.environ['TYAPP_DATABASE_URL'],connect_timeout=10,autocommit=True)
  if not DATABASE_LOCK.execute("SELECT pg_try_advisory_lock(hashtext(current_database()), hashtext('yuqing-instance'))").fetchone()[0]:
   DATABASE_LOCK.close();raise SystemExit('该数据库已有应用实例运行，请先停止旧实例。')
 DATA.mkdir(parents=True,exist_ok=True);os.chmod(DATA,0o700)
 INSTANCE_LOCK=open(DATA/'instance.lock','a+b')
 try:
  if os.name=='nt':
   import msvcrt
   if INSTANCE_LOCK.tell()==0:INSTANCE_LOCK.write(b'0');INSTANCE_LOCK.flush()
   INSTANCE_LOCK.seek(0);msvcrt.locking(INSTANCE_LOCK.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl
   fcntl.flock(INSTANCE_LOCK.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError:raise SystemExit('同一数据目录已有实例运行；请勿重复启动，备份前须先停止服务。')

def init(create_accounts=True, create_rules=True):
 global CIPHER
 DATA.mkdir(parents=True,exist_ok=True);os.chmod(DATA,0o700);(DATA/'media').mkdir(exist_ok=True)
 key=os.environ.get('XIN_ENCRYPTION_KEY','').encode(); kp=DATA/'encryption.key'
 if not key:
  if kp.exists():key=kp.read_bytes()
  else:key=Fernet.generate_key();kp.write_bytes(key);os.chmod(kp,0o600)
 CIPHER=Fernet(key)
 with connect() as db:
  db.executescript('''
  PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS accounts(username TEXT PRIMARY KEY,password TEXT NOT NULL,roles TEXT NOT NULL,active INTEGER DEFAULT 1,failures INTEGER DEFAULT 0,locked_until REAL DEFAULT 0);
  CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,username TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
  CREATE TABLE IF NOT EXISTS rules(version INTEGER PRIMARY KEY,body TEXT NOT NULL,actor TEXT NOT NULL,time TEXT NOT NULL,note TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,body TEXT NOT NULL,version INTEGER NOT NULL,created_by TEXT NOT NULL,updated_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,case_id TEXT,version INTEGER,body TEXT,actor TEXT,time TEXT);
  CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),source_url TEXT NOT NULL,captured_at TEXT NOT NULL,sha256 TEXT NOT NULL,filename TEXT NOT NULL,mime TEXT NOT NULL,created_at TEXT NOT NULL,method TEXT NOT NULL,source_cell TEXT NOT NULL,note TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),record_version INTEGER NOT NULL,rule_version INTEGER NOT NULL,level TEXT NOT NULL,reviewer TEXT NOT NULL,note TEXT NOT NULL,score_json TEXT NOT NULL,time TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS overrides(id TEXT PRIMARY KEY,case_id TEXT NOT NULL,record_version INTEGER,rule_version INTEGER,level TEXT,requester TEXT,note TEXT,status TEXT,created_at TEXT);
  CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,time TEXT,actor TEXT,action TEXT,target TEXT,detail TEXT,prev TEXT,hash TEXT);
  CREATE TABLE IF NOT EXISTS exports(id TEXT PRIMARY KEY,body TEXT NOT NULL,sha256 TEXT NOT NULL,count INTEGER NOT NULL,actor TEXT NOT NULL,time TEXT NOT NULL,purpose TEXT NOT NULL,receipt TEXT);
  CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,body TEXT NOT NULL,actor TEXT,time TEXT,committed INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS login_limits(ip TEXT PRIMARY KEY,failures INTEGER,locked_until REAL);
  ''')
  if create_rules and not db.execute('SELECT 1 FROM rules').fetchone():
   db.execute('INSERT INTO rules VALUES(?,?,?,?,?)',(1,js(DEFAULT),'system',now(),'初始需求口径，试点使用前需业务审批'))
  if create_accounts and not db.execute('SELECT 1 FROM accounts').fetchone():
   creds=[]
   for name,roles in [('admin',['admin','analyst','exporter','viewer']),('reviewer',['reviewer','viewer'])]:
    password=secrets.token_urlsafe(14);db.execute('INSERT INTO accounts(username,password,roles) VALUES(?,?,?)',(name,pw_hash(password),js(roles)))
    creds.append(name+' : '+password)
   cp=DATA/'首次登录凭据.txt';cp.write_text('本机生成，不随系统压缩包分发。请登录后修改密码。\n'+'\n'.join(creds),encoding='utf-8');os.chmod(cp,0o600)
   audit(db,'system','初始化账号','','两套随机密码，账号与复核职责分离')
   print('\n首次登录凭据保存在：'+str(cp),flush=True)
  # Verify encryption key immediately; wrong key must fail instead of silently resetting data.
  row=db.execute('SELECT body FROM cases LIMIT 1').fetchone()
  if row:dec(row[0])

def add_seed():
 path=ROOT/'seed'/'samples.json'
 if not path.exists():return
 with LOCK,connect() as db:
  actor={'username':'样本导入','roles':['analyst']}
  for sample in json.loads(path.read_text()):
   cid='SRC-'+sample['cell']
   if db.execute('SELECT 1 FROM cases WHERE id=?',(cid,)).fetchone():
    db.execute('UPDATE evidence SET mime=? WHERE id=?',(sample.get('mime','image/jpeg'),'EV-'+sample['cell']));continue
   c=normalize_case({'title':sample['title'],'event_code':'2026-'+sample['cell'],'platform':sample['platform'],'url':sample['url'],'phone':'','binding':'pending','source_cell':'2026年明细!'+sample['cell'],'published_at':sample.get('published_at',''),'relevance_basis':'来自用户提供的热线舆情预警登记表；仅确认原文可访问，尚未核实作者身份及传播指标。','metrics':{},'rights_feature':'unknown'},actor)
   db.execute('INSERT INTO cases VALUES(?,?,?,?,?)',(cid,enc(c),1,'样本导入',now()))
   image=ROOT/'seed'/sample['image'];raw=image.read_bytes();eid='EV-'+sample['cell']; filename=eid+Path(sample['image']).suffix
   (DATA/'media'/filename).write_bytes(CIPHER.encrypt(raw))
   db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,cid,sample['url'],sample['captured_at'],digest(raw),filename,sample.get('mime','image/jpeg'),now(),'browser_verified',sample['cell'],'真实浏览器页面截图；仅裁去浏览器工具栏，不生成或重绘原文。当前访问时点留存，不是发帖当日历史快照。'))
   audit(db,'system','导入真实来源样本',cid,sample['cell']+'；未绑定手机号；未虚构评分')

class HTTPError(Exception):
 def __init__(self,status,message):self.status=status;self.message=message
class Handler(BaseHTTPRequestHandler):
 server_version='XinPilot/1.0'
 def log_message(self,fmt,*args):
  # Never log query strings, phones, credentials or request bodies.
  print(now(),self.address_string(),self.command,urlsplit(self.path).path,flush=True)
 def send(self,status,data,ctype='application/json; charset=utf-8',headers=None):
  raw=(js(data).encode() if not isinstance(data,bytes) else data)
  self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(raw)))
  for k,v in {'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",**(headers or {})}.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(raw)
 def body(self):
  if self.headers.get('Transfer-Encoding'):raise HTTPError(400,'不支持分块请求')
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:raise HTTPError(400,'长度错误')
  if n<=0 or n>MAX_BODY:raise HTTPError(413,'请求须为非空JSON，且不超过20MB')
  if self.headers.get_content_type()!='application/json':raise HTTPError(415,'仅支持JSON请求')
  try:
   result=json.loads(self.rfile.read(n),parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
   if not isinstance(result,dict):raise ValueError()
   return result
  except (ValueError,UnicodeError):raise HTTPError(400,'JSON格式错误')
 def actor(self,db,write=False):
  jar=cookies.SimpleCookie()
  try:jar.load(self.headers.get('Cookie',''))
  except cookies.CookieError:raise HTTPError(401,'请先登录')
  token=jar.get(routing.cookie_name()); row=None
  if token:row=db.execute('SELECT s.*,a.roles,a.active FROM sessions s JOIN accounts a ON s.username=a.username WHERE s.token=? AND s.expires>?',(digest(token.value.encode()),time.time())).fetchone()
  if not row or not row['active']:raise HTTPError(401,'登录已失效，请重新登录')
  if write:
   if not hmac.compare_digest(row['csrf'],self.headers.get('X-CSRF-Token','')):raise HTTPError(403,'请求校验失败，请刷新后重试')
   origin=self.headers.get('Origin')
   if origin and urlsplit(origin).netloc!=self.headers.get('Host'):raise HTTPError(403,'拒绝跨站写入')
  return {'username':row['username'],'roles':json.loads(row['roles']),'csrf':row['csrf'],'token':row['token']}
 def need(self,a,*roles):
  if not set(a['roles']).intersection(roles):raise HTTPError(403,'当前账号无此权限')
 def do_GET(self):self.dispatch('GET')
 def do_POST(self):self.dispatch('POST')
 def do_PUT(self):self.dispatch('PUT')
 def do_DELETE(self):self.send(405,{'error':'不支持删除。档案与证据按保留策略管理。'})
 def dispatch(self,method):
  try:
   path=urlsplit(self.path).path;query=parse_qs(urlsplit(self.path).query)
   # DNS-rebinding guard, accepts only configured Host names (exact hostname, port ignored).
   host=self.headers.get('Host','').split(':')[0]
   try:self.route_base=routing.route_base(self.headers,self.client_address[0])
   except ValueError:raise HTTPError(400,'入口路径配置无效')
   allowed=os.environ.get('XIN_ALLOWED_HOSTS','127.0.0.1,localhost').split(',')
   if host not in allowed:raise HTTPError(403,'Host不在允许列表')
   if method=='GET' and path=='/health':
    try:
     with connect() as health_db:health_db.execute('SELECT 1').fetchone()
     if not os.access(DATA,os.W_OK):raise OSError()
    except Exception:return self.send(503,{'status':'unavailable'})
    return self.send(200,{'status':'ok','version':'1.0-pilot'})
   if method=='GET' and (path in ('/','/login') or path.startswith('/static/')):
    rel='static/index.html' if path in ('/','/login') else path.lstrip('/');p=(ROOT/rel).resolve()
    if not p.is_relative_to(ROOT/'static') or not p.is_file():raise HTTPError(404,'文件不存在')
    types={'.html':'text/html; charset=utf-8','.js':'application/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.png':'image/png','.svg':'image/svg+xml'}
    raw=p.read_bytes()
    if p.name=='index.html':raw=raw.replace(b'<!--APP_BASE-->',('<base href="'+escape(routing.cookie_path(self.route_base),quote=True)+'">').encode())
    return self.send(200,raw,types.get(p.suffix,'application/octet-stream'))
   with LOCK,connect() as db:
    if path=='/api/login' and method=='POST':return self.login(db,self.body())
    a=self.actor(db,method!='GET')
    if path=='/api/me' and method=='GET':return self.send(200,{k:a[k] for k in ('username','roles','csrf')})
    if path=='/api/logout' and method=='POST':
     db.execute('DELETE FROM sessions WHERE token=?',(a['token'],));audit(db,a['username'],'退出登录');db.commit();return self.send(200,{'ok':True},headers={'Set-Cookie':routing.cookie_name()+'=; HttpOnly; SameSite=Strict; Path='+routing.cookie_path(self.route_base)+'; Max-Age=0'+('; Secure' if os.environ.get('XIN_SECURE_COOKIE')=='1' else '')})
    if path=='/api/password' and method=='POST':
     b=self.body();row=db.execute('SELECT password FROM accounts WHERE username=?',(a['username'],)).fetchone()
     if not pw_ok(str(b.get('old','')),row[0]):raise Invalid('原密码不正确')
     p=b.get('password','');self.password_valid(p)
     db.execute('UPDATE accounts SET password=? WHERE username=?',(pw_hash(p),a['username']));db.execute('DELETE FROM sessions WHERE username=?',(a['username'],));audit(db,a['username'],'修改密码并注销全部会话');db.commit();return self.send(200,{'ok':True})
    if path=='/api/accounts':
     self.need(a,'admin')
     if method=='GET':return self.send(200,[dict(r,roles=json.loads(r['roles'])) for r in db.execute('SELECT username,roles,active FROM accounts')])
     if method=='POST':
      b=self.body();u=b.get('username','');p=b.get('password','');roles=b.get('roles',[])
      if not re.fullmatch('[A-Za-z0-9_.-]{3,40}',u):raise Invalid('账号须为3–40位字母数字或_.-')
      self.password_valid(p)
      if not isinstance(roles,list) or not roles or any(not isinstance(x,str) for x in roles) or not set(roles)<=ROLES:raise Invalid('角色无效')
      if db.execute('SELECT 1 FROM accounts WHERE username=?',(u,)).fetchone():raise Invalid('账号已存在')
      db.execute('INSERT INTO accounts(username,password,roles) VALUES(?,?,?)',(u,pw_hash(p),js(roles)));audit(db,a['username'],'创建账号',u,','.join(roles));db.commit();return self.send(201,{'ok':True})
    if path.startswith('/api/accounts/') and method=='PUT':
     self.need(a,'admin');u=path.split('/')[-1];b=self.body()
     if u==a['username']:raise Invalid('不能在此停用或改动自己的角色')
     roles=b.get('roles',[])
     if not isinstance(roles,list) or not roles or any(not isinstance(x,str) for x in roles) or not set(roles)<=ROLES:raise Invalid('角色无效')
     if not db.execute('SELECT 1 FROM accounts WHERE username=?',(u,)).fetchone():raise Invalid('账号不存在')
     db.execute('UPDATE accounts SET active=?,roles=? WHERE username=?',(int(bool(b.get('active'))),js(roles),u));db.execute('DELETE FROM sessions WHERE username=?',(u,));audit(db,a['username'],'调整账号',u,'角色或启用状态调整，既有会话已撤销');db.commit();return self.send(200,{'ok':True})
    if path=='/api/rules':
     if method=='GET':return self.send(200,{'current':read_rules(db),'history':[dict(x) for x in db.execute('SELECT version,actor,time,note FROM rules ORDER BY version DESC')]})
     if method=='POST':
      self.need(a,'admin');b=self.body();r=read_rules(db)
      if b.get('expected_version')!=r['version']:raise HTTPError(409,'规则已被他人更新，请刷新')
      new=validate_rules(b);note=str(b.get('note','')).strip()
      if not note:raise Invalid('规则发布必须填写变更依据')
      v=r['version']+1;db.execute('INSERT INTO rules VALUES(?,?,?,?,?)',(v,js(new),a['username'],now(),note));audit(db,a['username'],'发布评分规则','R'+str(v),note);db.commit();return self.send(201,{'version':v})
    if path=='/api/cases':
     if method=='GET':
      vals=[case_view(db,loadcase(db,x[0]),a) for x in db.execute('SELECT id FROM cases ORDER BY updated_at DESC')]
      q=query.get('q',[''])[0].strip().lower();status=query.get('status',[''])[0]
      if q:
       vals=[x for x in vals if q in js(x).lower()]
      if status:vals=[x for x in vals if x['status']==status or (x['review'] and x['review']['level']==status)]
      return self.send(200,{'items':vals,'total':len(vals)})
     if method=='POST':
      self.need(a,'admin','analyst');c=normalize_case(self.body(),a);cid=save_case(db,c,a);db.commit();return self.send(201,case_view(db,loadcase(db,cid),a,True))
    if path.startswith('/api/cases/'):
     seg=path.split('/');cid=seg[3];c=loadcase(db,cid)
     if len(seg)==4:
      if method=='GET':
       audit(db,a['username'],'查看档案',cid);db.commit();return self.send(200,case_view(db,c,a,True))
      if method=='PUT':
       self.need(a,'admin','analyst');b=self.body()
       if b.get('expected_version')!=c['version']:raise HTTPError(409,'档案已更新，请刷新；不会覆盖他人的修改')
       new=normalize_case(b,a,c);ev=evidence_for(db,cid)
       if any(ref not in ev for item in new['metrics'].values() for ref in item['evidence']):raise Invalid('指标引用了不属于本档案的证据')
       save_case(db,new,a,c);db.commit();return self.send(200,case_view(db,loadcase(db,cid),a,True))
     action=seg[4] if len(seg)==5 else ''
     if action=='history' and method=='GET':
      self.need(a,'admin','analyst','reviewer');return self.send(200,[{'version':x['version'],'actor':x['actor'],'time':x['time']} for x in db.execute('SELECT version,actor,time FROM history WHERE case_id=? ORDER BY id DESC',(cid,))])
     if action=='evidence' and method=='POST':return self.upload(db,a,c,self.body())
     if action=='review' and method=='POST':return self.review(db,a,c,self.body())
     if action=='summary' and method=='POST':
      # No LLM call: explicit evidence-based local template.
      self.need(a,'admin','analyst','reviewer');s=score(c,read_rules(db),evidence_for(db,cid))
      text=f"本事件已留存{len(evidence_for(db,cid))}份证据。传播得分区间{s['lo']}–{s['hi']}，"+('建议'+s['suggested'] if s['suggested'] else '跨等级区间，暂不定级')+'。待补充：'+('、'.join(s['missing']) or '无')+'。仅供人工复核，不判断投诉合理性或职业动机。'
      audit(db,a['username'],'生成本地辅助摘要',cid,'模板引擎，未调用外部AI服务');db.commit();return self.send(200,{'text':text,'engine':'local-template','external_ai':False})
    if path.startswith('/api/overrides/') and method=='POST':
     self.need(a,'reviewer');oid=path.split('/')[-1];b=self.body();req=db.execute('SELECT * FROM overrides WHERE id=?',(oid,)).fetchone()
     if not req or req['status']!='pending':raise Invalid('申请不存在或已处理')
     c=loadcase(db,req['case_id']);r=read_rules(db)
     if a['username'] in (req['requester'],c['created_by']) or db.execute('SELECT 1 FROM history WHERE case_id=? AND actor=?',(c['id'],a['username'])).fetchone():raise Invalid('覆盖审批须由独立复核人员执行')
     if c['version']!=req['record_version'] or r['version']!=req['rule_version']:raise HTTPError(409,'申请版本已过期，请重新申请')
     if current_review(db,c,r):raise Invalid('此版本已有确认结果，不能再次覆盖')
     note=str(b.get('note','')).strip()
     if not note:raise Invalid('请填写审批意见')
     s=score(c,r,evidence_for(db,c['id']))
     if s['gates']:raise Invalid('绑定或基础证据门槛未满足，不可审批')
     if b.get('approve'):
      self.insert_review(db,c,r,req['level'],a,req['note']+'；独立审批：'+note,s)
     db.execute('UPDATE overrides SET status=? WHERE id=?',('approved' if b.get('approve') else 'rejected',oid));audit(db,a['username'],'审批等级覆盖',c['id'],oid+'；'+note);db.commit();return self.send(200,{'ok':True})
    if path=='/api/overrides' and method=='GET':return self.send(200,[dict(x) for x in db.execute("SELECT * FROM overrides ORDER BY created_at DESC")])
    if path.startswith('/api/evidence/') and method=='GET':
     eid=path.split('/')[-1];row=db.execute('SELECT * FROM evidence WHERE id=?',(eid,)).fetchone()
     if not row:raise HTTPError(404,'证据不存在')
     raw=CIPHER.decrypt((DATA/'media'/row['filename']).read_bytes())
     if digest(raw)!=row['sha256']:raise HTTPError(500,'证据校验失败，禁止展示')
     audit(db,a['username'],'查看证据',row['case_id'],eid);db.commit();return self.send(200,raw,row['mime'])
    if path=='/api/import/preview' and method=='POST':return self.import_preview(db,a,self.body())
    if path=='/api/import/commit' and method=='POST':
     self.need(a,'admin','analyst');b=self.body();rec=db.execute('SELECT * FROM imports WHERE id=? AND committed=0',(b.get('id',''),)).fetchone()
     if not rec or rec['actor']!=a['username']:raise Invalid('预览批次不存在、已提交或不是本人创建')
     rows=dec(rec['body']);created=[]
     for c in rows:created.append(save_case(db,c,a))
     db.execute('UPDATE imports SET committed=1 WHERE id=?',(rec['id'],));audit(db,a['username'],'提交导入批次',rec['id'],str(len(created))+'条');db.commit();return self.send(201,{'created':created})
    if path=='/api/exports' and method=='GET':
     self.need(a,'admin','exporter');return self.send(200,[dict({k:x[k] for k in ('id','sha256','count','actor','time','purpose','receipt')},stale=export_stale(db,x)) for x in db.execute('SELECT * FROM exports ORDER BY time DESC')])
    if path=='/api/exports' and method=='POST':return self.export(db,a,self.body())
    if path.startswith('/api/exports/'):
     self.need(a,'admin','exporter');seg=path.split('/');eid=seg[3];row=db.execute('SELECT * FROM exports WHERE id=?',(eid,)).fetchone()
     if not row:raise Invalid('导出批次不存在')
     if len(seg)==5 and seg[4]=='download' and method=='GET':
      raw=CIPHER.decrypt(row['body'].encode());audit(db,a['username'],'下载导出文件',eid);db.commit();return self.send(200,raw,'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="'+eid+'.csv"'})
     if len(seg)==5 and seg[4]=='receipt' and method=='POST':
      b=self.body()
      if not row['purpose'].startswith('正式客服导入：'):raise Invalid('脱敏报表不是客服导入批次，不接受同步回执')
      if b.get('sha256')!=row['sha256'] or b.get('accepted')!=row['count']:raise Invalid('回执摘要或成功条数不匹配，不能确认完成')
      ref=str(b.get('reference','')).strip()
      if not ref:raise Invalid('需提供目标系统回执号/工单号')
      if row['receipt']:raise Invalid('该批次已有回执，不可覆盖')
      receipt={'reference':ref,'accepted':b['accepted'],'actor':a['username'],'time':now(),'type':'manual_receipt','note':'人工登记，未自动核验目标系统'}
      db.execute('UPDATE exports SET receipt=? WHERE id=?',(js(receipt),eid));audit(db,a['username'],'登记人工回执',eid,ref);db.commit();return self.send(200,receipt)
    if path=='/api/audit' and method=='GET':
     self.need(a,'admin');prev='0'*64;valid=True;allrows=[]
     for x in db.execute('SELECT * FROM audit ORDER BY id'):
      x=dict(x);valid=valid and x['prev']==prev and x['hash']==digest(js([x[k] for k in ('time','actor','action','target','detail','prev')]).encode());prev=x['hash'];allrows.append(x)
     return self.send(200,{'chain_valid':valid,'items':allrows[-500:][::-1],'total':len(allrows),'note':'本机哈希链辅助发现改动，不等于第三方可信时间戳或防篡改审计平台'})
   raise HTTPError(404,'接口不存在或请求方法不支持')
  except HTTPError as e:self.send(e.status,{'error':e.message})
  except Invalid as e:self.send(422,{'error':str(e)})
  except database.IntegrityError:self.send(409,{'error':'数据冲突，请刷新后重试'})
  except Exception as e:
   print('SERVER ERROR',type(e).__name__,flush=True);self.send(500,{'error':'处理失败，原事务未提交。请联系管理员检查服务日志。'})
 def password_valid(self,p):
  if not isinstance(p,str) or len(p)<12 or len(p)>128:raise Invalid('密码长度须为12–128位')
 def login(self,db,b):
  u=str(b.get('username',''));p=str(b.get('password',''));ip=self.client_address[0];t=time.time()
  limit=db.execute('SELECT * FROM login_limits WHERE ip=?',(ip,)).fetchone()
  if limit and limit['locked_until']>t:raise HTTPError(429,'尝试过多，请15分钟后重试')
  row=db.execute('SELECT * FROM accounts WHERE username=?',(u,)).fetchone()
  if row and row['locked_until']>t:raise HTTPError(429,'账号暂时锁定，请15分钟后重试')
  valid=pw_ok(p,row['password']) if row else pw_ok(p,pw_hash('constant-dummy-password','0'*32))
  if not valid or not row or not row['active']:
   fail=(row['failures'] if row else 0)+1
   if row:db.execute('UPDATE accounts SET failures=?,locked_until=? WHERE username=?',(fail,t+900 if fail>=5 else 0,u))
   n=(limit['failures'] if limit else 0)+1;db.execute('INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until',(ip,n,t+900 if n>=20 else 0));audit(db,'anonymous','登录失败','','未记录输入账号和密码');db.commit();raise HTTPError(401,'账号或密码错误')
  token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32);db.execute('UPDATE accounts SET failures=0,locked_until=0 WHERE username=?',(u,));db.execute('DELETE FROM login_limits WHERE ip=?',(ip,));db.execute('DELETE FROM sessions WHERE expires<?',(t,));db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token.encode()),u,csrf,t+8*3600));audit(db,u,'登录成功');db.commit()
  secure='; Secure' if os.environ.get('XIN_SECURE_COOKIE')=='1' else ''
  return self.send(200,{'username':u,'roles':json.loads(row['roles']),'csrf':csrf},headers={'Set-Cookie':routing.cookie_name()+'='+token+'; HttpOnly; SameSite=Strict; Path='+routing.cookie_path(self.route_base)+'; Max-Age=28800'+secure})
 def upload(self,db,a,c,b):
  self.need(a,'admin','analyst')
  if b.get('expected_version')!=c['version']:raise HTTPError(409,'档案已更新，请刷新后上传')
  source=b.get('source_url','')
  if source!=c['url']:raise Invalid('截图来源必须与本档案原文链接一致')
  stamp=str(b.get('captured_at',''))
  try:
   dt=datetime.fromisoformat(stamp)
   if not dt.tzinfo or dt.timestamp()>time.time()+300:raise ValueError()
  except ValueError:raise Invalid('留存时间需为含时区的实际时间，不能填写未来时间')
  try:
   raw=base64.b64decode(b.get('data',''),validate=True)
   if len(raw)>12*1024*1024:raise Invalid('单张图片上限12MB')
   with Image.open(io.BytesIO(raw)) as im:
    if im.format not in ('PNG','JPEG','WEBP') or im.width*im.height>40000000:raise Invalid('仅接受PNG/JPEG/WebP，像素数不超过4000万')
    fmt=im.format;im.verify()
  except Invalid:raise
  except Exception:raise Invalid('文件不是有效的PNG/JPEG/WebP图片')
  sha=digest(raw)
  if db.execute('SELECT 1 FROM evidence WHERE case_id=? AND sha256=?',(c['id'],sha)).fetchone():raise Invalid('该图片已保存，不重复入库')
  eid='EV-'+secrets.token_hex(8);ext={'PNG':'.png','JPEG':'.jpg','WEBP':'.webp'}[fmt];fn=eid+ext;note=str(b.get('note','')).strip()
  if not note:raise Invalid('需填写截图核验说明（谁、何时、如何取得）')
  (DATA/'media'/fn).write_bytes(CIPHER.encrypt(raw))
  db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,c['id'],source,stamp,sha,fn,'image/'+('jpeg' if fmt=='JPEG' else fmt.lower()),now(),'manual_upload',c.get('source_cell',''),note))
  # Appending evidence changes the dossier version and invalidates current confirmation.
  base={k:v for k,v in c.items() if k not in ('id','version','created_by','updated_at')};save_case(db,base,a,c);audit(db,a['username'],'追加快照证据',c['id'],eid+' / SHA256 '+sha);db.commit();return self.send(201,{'id':eid,'sha256':sha,'version':c['version']+1})
 def insert_review(self,db,c,r,level,a,note,s):
  db.execute('INSERT INTO reviews(case_id,record_version,rule_version,level,reviewer,note,score_json,time) VALUES(?,?,?,?,?,?,?,?)',(c['id'],c['version'],r['version'],level,a['username'],note,js(s),now()))
 def review(self,db,a,c,b):
  self.need(a,'reviewer');r=read_rules(db)
  if b.get('expected_version')!=c['version'] or b.get('rule_version')!=r['version']:raise HTTPError(409,'复核版本已变化，请刷新')
  if a['username']==c['created_by'] or db.execute('SELECT 1 FROM history WHERE case_id=? AND actor=?',(c['id'],a['username'])).fetchone():raise Invalid('复核人不能是档案创建人或历史修改人')
  if current_review(db,c,r):raise Invalid('此版本已经确认；如需调整，请更新档案后重新复核')
  s=score(c,r,evidence_for(db,c['id']));lv=b.get('level');note=str(b.get('note','')).strip()
  if s['gates']:raise Invalid('无法复核：'+'；'.join(s['gates']))
  if lv not in ('红色','橙色','黄色','蓝色') or not note:raise Invalid('需选择等级并填写复核依据')
  if lv!=s['suggested']:
   if not b.get('override'):raise Invalid('与建议不一致或证据跨档，须申请独立覆盖审批')
   oid='OV-'+secrets.token_hex(6);db.execute('INSERT INTO overrides VALUES(?,?,?,?,?,?,?,?,?)',(oid,c['id'],c['version'],r['version'],lv,a['username'],note,'pending',now()));audit(db,a['username'],'申请等级覆盖',c['id'],oid+' / '+lv);db.commit();return self.send(202,{'pending':True,'id':oid})
  if not s['eligible']:raise Invalid('证据不足，不可直接确认')
  self.insert_review(db,c,r,lv,a,note,s);audit(db,a['username'],'确认风险等级',c['id'],lv+' / R'+str(r['version'])+' / V'+str(c['version']));db.commit();return self.send(201,{'ok':True})
 def import_preview(self,db,a,b):
  self.need(a,'admin','analyst');fmt=b.get('format');rows=[]
  if fmt=='csv':
   text=str(b.get('text','')).lstrip('\ufeff');reader=csv.DictReader(io.StringIO(text))
   for n,row in enumerate(reader,2):rows.append((n,row))
  elif fmt=='xlsx_h':
   try:
    raw=base64.b64decode(b.get('data',''),validate=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
     if len(z.infolist())>10000 or sum(x.file_size for x in z.infolist())>100*1024*1024:raise Invalid('工作簿展开后过大')
     ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'};wb=ET.fromstring(z.read('xl/workbook.xml'));relations=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'));targets={x.attrib['Id']:x.attrib['Target'] for x in relations}
     sheets=wb.find('m:sheets',ns);sheet=next(x for x in sheets if x.attrib['name'] in ('2026年明细','26年明细'))
     target=targets[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']];target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
     strings=[]
     if 'xl/sharedStrings.xml' in z.namelist():
      strings=[''.join(t.text or '' for t in si.iter('{'+ns['m']+'}t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))]
     xml=ET.fromstring(z.read(target))
     relpath=str(Path(target).parent/'_rels'/(Path(target).name+'.rels'))
     links={}
     if relpath in z.namelist():
      sheetrels={x.attrib['Id']:x.attrib.get('Target','') for x in ET.fromstring(z.read(relpath))}
      for link in xml.findall('.//m:hyperlink',ns):
       rid=link.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
       if rid in sheetrels:links[link.attrib.get('ref','')]=sheetrels[rid]
     cells={cell.attrib['r']:cell for cell in xml.findall('.//m:sheetData/m:row/m:c',ns)}
     for pos in links:
      if pos not in cells:cells[pos]=ET.Element('c',{'r':pos})
     for cell in cells.values():
      pos=cell.attrib['r']
      if not re.fullmatch('H[0-9]+',pos) or pos=='H1':continue
      v=cell.find('m:v',ns);value=v.text if v is not None else ''.join(x.text or '' for x in cell.findall('.//m:t',ns))
      if cell.attrib.get('t')=='s':value=strings[int(value)]
      urls=list(dict.fromkeys(re.findall(r'https?://[^\s<>"，]+',(value or '')+' '+links.get(pos,''))))
      for idx,u in enumerate(urls):rows.append((pos,{'事件编号':'2026-'+pos+('-'+str(idx+1) if idx else ''),'事件标题':'待核查原文 · '+pos,'舆情网址':u,'来源单元格':sheet.attrib['name']+'!'+pos}))
   except Invalid:raise
   except Exception:raise Invalid('无法读取工作簿，请使用2026年明细H列或CSV模板')
  else:raise Invalid('格式须为csv或xlsx_h')
  if not rows:raise Invalid('未找到可导入行')
  if len(rows)>3000:raise Invalid('单批上限3000条，请分批导入')
  existing={dec(x[0])['url'] for x in db.execute('SELECT body FROM cases')};seen=set();ready=[];errors=[];duplicates=[]
  for line,row in rows:
   try:
    url=(row.get('舆情网址') or '').strip()
    if url in existing or url in seen:duplicates.append(str(line));continue
    c=normalize_case({'event_code':row.get('事件编号',''),'title':row.get('事件标题',''),'url':url,'phone':row.get('手机号',''),'platform':row.get('平台',''),'phone_source':row.get('手机号来源',''),'source_cell':row.get('来源单元格',''),'binding':'pending','metrics':{},'rights_feature':'unknown'},a)
    ready.append(c);seen.add(url)
   except Invalid as e:errors.append({'row':line,'error':str(e)})
  if not ready: return self.send(200,{'id':None,'valid':0,'errors':errors,'duplicates':duplicates,'preview':[]})
  bid='IM-'+secrets.token_hex(6);db.execute('INSERT INTO imports VALUES(?,?,?,?,0)',(bid,enc(ready),a['username'],now()));audit(db,a['username'],'预览导入',bid,str(len(ready))+'条有效 / '+str(len(errors))+'条错误');db.commit()
  return self.send(200,{'id':bid,'valid':len(ready),'errors':errors,'duplicates':duplicates,'preview':[{'title':x['title'],'source_cell':x['source_cell'],'url':x['url'],'phone':('已提供，待核实' if x['phone'] else '待绑定')} for x in ready[:30]]})
 def export(self,db,a,b):
  if b.get('kind') not in ('formal','masked'):raise Invalid('导出类型须为formal或masked')
  full=b.get('kind')=='formal'
  self.need(a,'admin','exporter')
  purpose=str(b.get('purpose','')).strip()
  if not purpose:raise Invalid('需填写导出用途')
  if full and b.get('authorized') is not True:raise Invalid('完整手机号导出必须确认业务授权')
  r=read_rules(db);headers=['档案编号','事件编号','手机号' if full else '脱敏手机号','最终域外等级','得分下界','得分上界','规则版本','档案版本','域内等级','复核人','复核时间','原文链接']
  for _,title,_,_ in DIMS:headers += [title+'原始值',title+'得分下界',title+'得分上界',title+'依据']
  headers+=['维权特征（不计分）','维权依据','导出批次'];out=[];bid='EX-'+secrets.token_hex(6)
  for x in db.execute('SELECT id FROM cases ORDER BY id'):
   c=loadcase(db,x[0]);s=score(c,r,evidence_for(db,c['id']));rev=current_review(db,c,r)
   if full and (not rev or s['gates']):continue
   row=[c['id'],c['event_code'],c['phone'] if full else (c['phone'][:3]+'****'+c['phone'][-4:] if c['phone'] else ''),rev['level'] if rev else '未确认',s['lo'],s['hi'],'R'+str(r['version']),c['version'],c.get('internal_level',''),rev['reviewer'] if rev else '',rev['time'] if rev else '',c['url']]
   for p in s['parts']:row += [p['value'] if p['known'] else '未知',p['lo'],p['hi'],p['why']]
   row += [c.get('rights_feature','unknown'),c.get('rights_basis',''),bid];out.append(row)
  if not out:raise Invalid('无可导出记录。正式导出仅包含当前版本绑定已核实、人工复核确认的结果。')
  stream=io.StringIO(newline='');writer=csv.writer(stream)
  def safe(x):
   v=str(x)
   return "'"+v if v.lstrip().startswith(('=','+','-','@','\t','\r')) else v
  writer.writerow(headers)
  for row in out:writer.writerow([safe(v) for v in row])
  raw=('\ufeff'+stream.getvalue()).encode();sha=digest(raw)
  db.execute('INSERT INTO exports VALUES(?,?,?,?,?,?,?,NULL)',(bid,CIPHER.encrypt(raw).decode(),sha,len(out),a['username'],now(),('正式客服导入：' if full else '脱敏解释报表：')+purpose));audit(db,a['username'],'生成正式导出' if full else '生成脱敏报表',bid,str(len(out))+'条 / '+sha);db.commit();return self.send(201,{'id':bid,'count':len(out),'sha256':sha,'download':'api/exports/'+bid+'/download','status':'已生成，尚无目标系统回执'})

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--host',default=os.environ.get('XIN_HOST','127.0.0.1'));parser.add_argument('--port',type=int,default=int(os.environ.get('XIN_PORT','8765')));parser.add_argument('--no-seed',action='store_true');args=parser.parse_args()
 if os.environ.get('XIN_REQUIRE_POSTGRES')=='1' and not database.is_postgres():raise SystemExit('缺少 TYAPP_DATABASE_URL，拒绝使用临时 SQLite')
 acquire_instance_lock()
 init()
 if not args.no_seed:add_seed()
 print('投诉治理“换芯计划”试点系统 http://'+args.host+':'+str(args.port),flush=True)
 ThreadingHTTPServer.daemon_threads=True
 ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()
if __name__=='__main__':main()
