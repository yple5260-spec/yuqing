"""隔离临时数据库测试；测试手机号和数值不写入演示样本。"""
import base64, copy, csv, hashlib, http.client, io, json, os, subprocess, sys, tempfile, time, unittest, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scoring import DEFAULT,DIMS,score,level,validate_rules,Invalid
class ScoringTests(unittest.TestCase):
 def test_grade_boundaries(self):
  for n,l in [(0,'蓝色'),(24,'蓝色'),(25,'黄色'),(49,'黄色'),(50,'橙色'),(74,'橙色'),(75,'红色'),(100,'红色')]:self.assertEqual(level(n,DEFAULT),l)
 def test_unknown_is_not_zero(self):
  s=score({},DEFAULT,{});self.assertEqual((s['lo'],s['hi'],s['suggested']),(0,100,None));self.assertFalse(s['eligible'])
 def test_metrics_without_evidence_unknown(self):
  s=score({'metrics':{'fans':{'value':999999}}},DEFAULT,{});self.assertFalse(s['parts'][2]['known'])
 def test_rate_needs_pair(self):
  c={'metrics':{'rate':{'value':1500,'evidence':['a'],'basis':'累计数不能替代速率','observed_at':'2026-01-01T00:00:00+08:00'}}}
  self.assertFalse(score(c,DEFAULT,{'a':{}})['parts'][1]['known'])
 def test_hourly_rate(self):
  c={'metrics':{'rate':{'value':100,'evidence':['a','b'],'basis':'双时点差分','observed_at':'2026-01-01T02:00:00+08:00','pair':{'ids':['a','b'],'totals':[10,210]}}}}
  ev={'a':{'source_url':'https://example.test/p','captured_at':'2026-01-01T00:00:00+08:00'},'b':{'source_url':'https://example.test/p','captured_at':'2026-01-01T02:00:00+08:00'}}
  self.assertEqual(score(c,DEFAULT,ev)['parts'][1]['lo'],20)
  ev['b']['source_url']='https://example.test/other';self.assertFalse(score(c,DEFAULT,ev)['parts'][1]['known'])
 def test_invalid_rule_total(self):
  r=copy.deepcopy(DEFAULT);r['dims']['fans']['points'][-1]=20
  with self.assertRaises(Invalid):validate_rules(r)
 def test_rights_do_not_score(self):
  self.assertEqual(score({'rights_feature':'observed'},DEFAULT,{})['hi'],100)
class EndToEnd(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  import socket
  s=socket.socket();s.bind(('127.0.0.1',0));cls.port=s.getsockname()[1];s.close()
  cls.tmp=tempfile.TemporaryDirectory();cls.env=dict(os.environ,XIN_DATA_DIR=cls.tmp.name,XIN_ENV_FILE=str(Path(cls.tmp.name)/'missing.env'),TYAPP_DATABASE_URL='',XIN_ENCRYPTION_KEY='',XIN_REQUIRE_POSTGRES='0')
  for name in ('ENDPOINT','REGION','BUCKET','ACCESS_KEY_ID','SECRET_ACCESS_KEY','FORCE_PATH_STYLE'):cls.env.pop('TYAPP_S3_'+name,None)
  cls.log=open(Path(cls.tmp.name)/'test-server.log','w');cls.proc=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(cls.port)],env=cls.env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:
    if cls.req('GET','/health')[0]==200:break
   except OSError:pass
   time.sleep(.1)
  else:raise RuntimeError('测试服务未启动')
  cls.creds=dict(x.split(' : ') for x in (Path(cls.tmp.name)/'首次登录凭据.txt').read_text().splitlines()[1:]);cls.sessions={}
  cls.login('admin');cls.login('reviewer')
 @classmethod
 def tearDownClass(cls):cls.proc.terminate();cls.proc.wait(10);cls.log.close();cls.tmp.cleanup()
 @classmethod
 def req(cls,method,path,data=None,who=None,csrf=True,headers=None):
  h={'Content-Type':'application/json'}
  if who:
   h['Cookie']=cls.sessions[who]['cookie']
   if csrf:h['X-CSRF-Token']=cls.sessions[who]['csrf']
  h.update(headers or {});c=http.client.HTTPConnection('127.0.0.1',cls.port,timeout=20)
  c.request(method,path,json.dumps(data,ensure_ascii=False).encode() if data is not None else None,h);r=c.getresponse();raw=r.read();hs=dict(r.getheaders());st=r.status;c.close()
  try:body=json.loads(raw)
  except (ValueError,UnicodeError):body=raw
  return st,body,hs
 @classmethod
 def login(cls,who):
  st,b,h=cls.req('POST','/api/login',{'username':who,'password':cls.creds[who]});assert st==200,(st,b)
  cls.sessions[who]={'cookie':h['Set-Cookie'].split(';')[0],'csrf':b['csrf']}
 def test_01_access_security(self):
  self.assertEqual(self.req('GET','/api/cases')[0],401)
  self.assertEqual(self.req('POST','/api/cases',{},'admin',False)[0],403)
  self.assertEqual(self.req('GET','/health',headers={'Host':'evil.test'})[0],403)
  self.assertEqual(self.req('POST','/api/cases',[], 'admin')[0],400)
  self.assertEqual(self.req('GET','/api/accounts',who='reviewer')[0],403)
 def test_02_twelve_authentic_samples(self):
  st,b,_=self.req('GET','/api/cases',who='admin');self.assertEqual(st,200);self.assertEqual(b['total'],12)
  for c in b['items']:self.assertFalse(c['phone']);self.assertIsNone(c['review']);self.assertEqual(c['evaluation']['hi'],100)
  _,c,_=self.req('GET','/api/cases/SRC-H44',who='admin');ev=c['evidence'][0]
  st,raw,_=self.req('GET','/api/evidence/'+ev['id'],who='admin');self.assertEqual(st,200);self.assertEqual(hashlib.sha256(raw).hexdigest(),ev['sha256']);self.assertEqual(raw,(ROOT/'seed/H44.jpg').read_bytes())
 def test_03_create_conflict_and_upload(self):
  d={'event_code':'TEST-ONLY','title':'仅自动化测试：不得作为真实用户','url':'https://example.test/unit','phone':'19900000000','phone_source':'隔离测试占位，不是真实用户','binding':'verified','binding_basis':'测试路径','relevance_basis':'自动化隔离测试','metrics':{}}
  st,c,_=self.req('POST','/api/cases',d,'admin');self.assertEqual(st,201,c);type(self).cid=c['id']
  self.assertEqual(self.req('POST','/api/cases',d,'admin')[0],422)
  self.assertEqual(self.req('PUT','/api/cases/'+self.cid,{'expected_version':99},'admin')[0],409)
  raw=(ROOT/'seed/H44.jpg').read_bytes();up={'expected_version':1,'source_url':d['url'],'captured_at':'2026-01-01T00:00:00+08:00','data':base64.b64encode(raw).decode(),'note':'隔离测试用原始图片，非新增演示样本'}
  st,b,_=self.req('POST','/api/cases/'+self.cid+'/evidence',up,'admin');self.assertEqual(st,201,b);type(self).eid=b['id'];up['expected_version']=2
  self.assertEqual(self.req('POST','/api/cases/'+self.cid+'/evidence',up,'admin')[0],422)
  self.assertNotIn(b'19900000000',(Path(self.tmp.name)/'xin.db').read_bytes())
  self.assertNotEqual((Path(self.tmp.name)/'media'/(self.eid+'.jpg')).read_bytes(),raw)
 def test_04_score_and_independent_review(self):
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin')
  metrics={k:{'value':v,'evidence':[self.eid],'observed_at':'2026-01-01T00:00:00+08:00','basis':'仅隔离测试数值，不用于演示画像'} for k,v in [('posts7',8),('fans',100000),('original30',8),('bigv','repost')]}
  st,c,_=self.req('PUT','/api/cases/'+self.cid,{'expected_version':c['version'],'metrics':metrics},'admin');self.assertEqual(st,200,c)
  self.assertEqual((c['evaluation']['lo'],c['evaluation']['hi']),(70,100));self.assertIsNone(c['evaluation']['suggested'])
  # 80–100 can be reached with altered approved points, but current missing rate crosses the threshold.
  body={'expected_version':c['version'],'rule_version':1,'level':'红色','note':'测试跨档覆盖申请','override':True}
  self.assertEqual(self.req('POST','/api/cases/'+self.cid+'/review',body,'admin')[0],403)
  st,b,_=self.req('POST','/api/cases/'+self.cid+'/review',body,'reviewer');self.assertEqual(st,202,b);type(self).oid=b['id']
  self.assertEqual(self.req('POST','/api/overrides/'+self.oid,{'approve':True,'note':'自审应失败'},'reviewer')[0],422)
  self.req('POST','/api/accounts',{'username':'reviewer2','password':'Test-Only-Password-12345','roles':['reviewer','viewer']},'admin');self.creds['reviewer2']='Test-Only-Password-12345';self.login('reviewer2')
  st,b,_=self.req('POST','/api/overrides/'+self.oid,{'approve':True,'note':'独立隔离测试批准'},'reviewer2');self.assertEqual(st,200,b)
 def test_05_export_receipt_and_mask(self):
  self.assertEqual(self.req('POST','/api/exports',{'kind':'formal','purpose':'测试'},'admin')[0],422)
  st,b,_=self.req('POST','/api/exports',{'kind':'formal','purpose':'隔离自动化测试','authorized':True},'admin');self.assertEqual(st,201,b);self.assertEqual(b['count'],1)
  st,raw,_=self.req('GET',b['download'],who='admin');self.assertEqual(st,200);self.assertEqual(hashlib.sha256(raw).hexdigest(),b['sha256']);self.assertIn('19900000000',raw.decode('utf-8-sig'));self.assertIn('每小时新增互动量依据',raw.decode('utf-8-sig'))
  endpoint='/api/exports/'+b['id']+'/receipt'
  self.assertEqual(self.req('POST',endpoint,{'sha256':'wrong','accepted':1,'reference':'TEST'},'admin')[0],422)
  self.assertEqual(self.req('POST',endpoint,{'sha256':b['sha256'],'accepted':1,'reference':'TEST-RECEIPT'},'admin')[0],200)
  self.assertEqual(self.req('POST',endpoint,{'sha256':b['sha256'],'accepted':1,'reference':'TEST-RECEIPT'},'admin')[0],422)
  st,b,_=self.req('POST','/api/exports',{'kind':'masked','purpose':'测试脱敏'},'admin');self.assertEqual(st,201)
  _,raw,_=self.req('GET',b['download'],who='admin');self.assertNotIn('19900000000',raw.decode('utf-8-sig'));self.assertIn('199****0000',raw.decode('utf-8-sig'))
  self.assertEqual(self.req('POST','/api/exports/'+b['id']+'/receipt',{'sha256':b['sha256'],'accepted':b['count'],'reference':'TEST'},'admin')[0],422)
 def test_06_change_invalidates_review(self):
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin');self.assertTrue(c['review'])
  st,c,_=self.req('PUT','/api/cases/'+self.cid,{'expected_version':c['version'],'quality_note':'测试更新'},'admin');self.assertEqual(st,200);self.assertIsNone(c['review']);self.assertTrue(c['reviews'])
  self.assertEqual(self.req('POST','/api/exports',{'kind':'formal','purpose':'测试','authorized':True},'admin')[0],422)
  _,batches,_=self.req('GET','/api/exports',who='admin');self.assertTrue(any(x['stale'] for x in batches))
 def test_07_csv_preview_commit(self):
  text='事件编号,事件标题,手机号,平台,舆情网址\nTEST-CSV,隔离测试导入,,测试,https://example.test/import\nDUP,同批重复,,测试,https://example.test/import\nBAD,错误手机号,abc,测试,https://example.test/bad\n'
  st,b,_=self.req('POST','/api/import/preview',{'format':'csv','text':text},'admin');self.assertEqual(st,200,b);self.assertEqual(b['valid'],1);self.assertEqual(len(b['errors']),1);self.assertEqual(len(b['duplicates']),1)
  self.assertEqual(self.req('POST','/api/import/commit',{'id':b['id']},'admin')[0],201)
  self.assertEqual(self.req('POST','/api/import/commit',{'id':b['id']},'admin')[0],422)
 def test_08_xlsx_hyperlink_only(self):
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:
   z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="2026年明细" sheetId="1" r:id="rId1"/></sheets></workbook>')
   z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
   z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData><row r="2"><c r="H2" t="inlineStr"><is><t>点击查看</t></is></c><c r="A2" t="inlineStr"><is><t>不应导入的其他列</t></is></c></row></sheetData><hyperlinks><hyperlink ref="H2" r:id="rId1"/></hyperlinks></worksheet>')
   z.writestr('xl/worksheets/_rels/sheet1.xml.rels','<Relationships><Relationship Id="rId1" Target="https://example.test/hyperlink" TargetMode="External"/></Relationships>')
  st,b,_=self.req('POST','/api/import/preview',{'format':'xlsx_h','data':base64.b64encode(buf.getvalue()).decode()},'admin');self.assertEqual(st,200,b);self.assertEqual(b['valid'],1);self.assertEqual(b['preview'][0]['source_cell'],'2026年明细!H2')
 def test_09_rule_version_and_audit(self):
  _,r,_=self.req('GET','/api/rules',who='admin');d=r['current'];d['expected_version']=d['version'];d['note']='测试规则发布'
  self.assertEqual(self.req('POST','/api/rules',d,'reviewer')[0],403)
  st,b,_=self.req('POST','/api/rules',d,'admin');self.assertEqual(st,201,b);self.assertEqual(b['version'],2)
  self.assertEqual(self.req('POST','/api/rules',d,'admin')[0],409)
  _,a,_=self.req('GET','/api/audit',who='admin');self.assertTrue(a['chain_valid']);self.assertGreater(a['total'],20)
 def test_10_disable_revokes_session(self):
  self.assertEqual(self.req('PUT','/api/accounts/reviewer2',{'roles':['viewer'],'active':False},'admin')[0],200)
  self.assertEqual(self.req('GET','/api/me',who='reviewer2')[0],401)
 def test_11_restart_persistence(self):
  self.proc.terminate();self.proc.wait(10);type(self).proc=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(self.port)],env=self.env,stdout=self.log,stderr=self.log)
  for _ in range(100):
   try:
    if self.req('GET','/health')[0]==200:break
   except OSError:pass
   time.sleep(.1)
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin');self.assertEqual(c['quality_note'],'测试更新')
 def test_12_normal_review_and_rule_invalidation(self):
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin')
  up={'expected_version':c['version'],'source_url':c['url'],'captured_at':'2026-01-01T01:00:00+08:00','data':base64.b64encode((ROOT/'seed/H46.jpg').read_bytes()).decode(),'note':'隔离自动化测试第二份图片，不用于真实档案'}
  st,b,_=self.req('POST','/api/cases/'+self.cid+'/evidence',up,'admin');self.assertEqual(st,201,b);second=b['id']
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin')
  metrics={k:{'value':'none' if k=='bigv' else 0,'evidence':[self.eid],'observed_at':'2026-01-01T01:00:00+08:00','basis':'隔离测试零值指标'} for k,_,_,_ in DIMS}
  metrics['rate']['pair']={'ids':[self.eid,second],'totals':[0,0]}
  _,c,_=self.req('PUT','/api/cases/'+self.cid,{'expected_version':c['version'],'metrics':metrics},'admin')
  self.assertEqual(c['evaluation']['suggested'],'蓝色');self.assertTrue(c['evaluation']['eligible'])
  body={'expected_version':c['version'],'rule_version':2,'level':'蓝色','note':'隔离测试正常复核'}
  st,b,_=self.req('POST','/api/cases/'+self.cid+'/review',body,'reviewer');self.assertEqual(st,201,b)
  self.assertEqual(self.req('POST','/api/cases/'+self.cid+'/review',body,'reviewer')[0],422)
  _,r,_=self.req('GET','/api/rules',who='admin');d=r['current'];d.update(expected_version=2,note='隔离测试验证变更后确认失效')
  self.assertEqual(self.req('POST','/api/rules',d,'admin')[0],201)
  _,c,_=self.req('GET','/api/cases/'+self.cid,who='admin');self.assertIsNone(c['review']);self.assertTrue(c['reviews'])
 def test_13_multiple_instance_blocked(self):
  p=subprocess.run([sys.executable,str(ROOT/'server.py'),'--port',str(self.port+1)],env=self.env,capture_output=True,timeout=5)
  self.assertNotEqual(p.returncode,0);self.assertIn('已有实例运行',p.stderr.decode()+p.stdout.decode())
 def test_14_backup_restore(self):
  backup=Path(self.tmp.name)/'backup.zip'
  p=subprocess.run([sys.executable,str(ROOT/'manage.py'),'backup',str(backup)],env=self.env,capture_output=True,timeout=5)
  self.assertNotEqual(p.returncode,0)
  self.proc.terminate();self.proc.wait(10)
  p=subprocess.run([sys.executable,str(ROOT/'manage.py'),'backup',str(backup)],env=self.env,capture_output=True,timeout=10);self.assertEqual(p.returncode,0,p.stderr.decode())
  p=subprocess.run([sys.executable,str(ROOT/'manage.py'),'verify-backup',str(backup)],env=self.env,capture_output=True,timeout=5);self.assertEqual(p.returncode,0,p.stderr.decode())
  restored=Path(self.tmp.name)/'restored';restored.mkdir()
  with zipfile.ZipFile(backup) as z:z.extractall(restored)
  type(self).proc=subprocess.Popen([sys.executable,str(ROOT/'server.py'),'--port',str(self.port)],env=dict(self.env,XIN_DATA_DIR=str(restored)),stdout=self.log,stderr=self.log)
  for _ in range(100):
   try:
    if self.req('GET','/health')[0]==200:break
   except OSError:pass
   time.sleep(.1)
  self.assertEqual(self.req('GET','/api/me',who='admin')[0],401)
  self.login('admin');_,c,_=self.req('GET','/api/cases/'+self.cid,who='admin');self.assertEqual(c['quality_note'],'测试更新');self.assertEqual(len(c['evidence']),2)
if __name__=='__main__':unittest.main(verbosity=2)
