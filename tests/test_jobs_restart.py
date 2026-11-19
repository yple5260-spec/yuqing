"""Kill and restart an actual isolated server while its synthetic collector is running."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = '''
import base64,io,os,time
from pathlib import Path
from PIL import Image
import server
def collect(url):
 if os.environ['QUEUE_TEST_MODE']=='interrupt':
  (server.DATA/'collector-entered').write_text('running')
  while True:time.sleep(.1)
 out=io.BytesIO();Image.new('RGB',(16,16),'white').save(out,format='PNG')
 return {'status':'ok','title':'Synthetic restart test','platform':'红网·百姓呼声',
  'published_at':'2026-01-01','captured_at':server.now(),'content':'Synthetic content',
  'nickname':'Test','fans':None,'metrics_note':'Synthetic only','data':base64.b64encode(out.getvalue()).decode()}
server.collection.collect=collect
server.main()
'''


class RestartTests(unittest.TestCase):
    def test_server_restart_resumes_one_task_without_duplicate_dossier(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
            env = dict(os.environ, XIN_ENV_FILE=str(directory/'absent.env'), XIN_DATA_DIR=tmp,
                TYAPP_DATABASE_URL='', XIN_REQUIRE_POSTGRES='0', XIN_ENCRYPTION_KEY='', XIN_SECURE_COOKIE='0',
                XIN_ALLOWED_HOSTS='127.0.0.1', PYTHONIOENCODING='utf-8', QUEUE_TEST_MODE='interrupt')
            for field in ('ENDPOINT','REGION','BUCKET','ACCESS_KEY_ID','SECRET_ACCESS_KEY','FORCE_PATH_STYLE'):
                env['TYAPP_S3_'+field] = ''
            executable = sys.executable
            if os.name == 'nt':
                # The Windows venv redirector can exit before its child releases file locks.
                executable = sys._base_executable
                env['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(Path(sys.prefix)/'Lib/site-packages')))
            log = (directory/'server.log').open('w', encoding='utf-8')
            proc = None
            def request(method, path, data=None, headers=None):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                conn.request(method, path, json.dumps(data).encode() if data is not None else None,
                             {'Content-Type':'application/json', **(headers or {})})
                response=conn.getresponse(); raw=response.read(); status=response.status; hs=dict(response.getheaders());conn.close()
                return status,json.loads(raw),hs
            def wait_for(fn):
                for _ in range(120):
                    try:
                        if fn():return
                    except OSError:pass
                    time.sleep(.05)
                self.fail('Isolated server did not reach expected state: '+(directory/'server.log').read_text(encoding='utf-8')[-1500:])
            def start():
                process=subprocess.Popen([executable,'-c',LAUNCH,'--host','127.0.0.1','--port',str(port),'--no-seed'],
                                         cwd=ROOT,env=env,stdout=log,stderr=log)
                wait_for(lambda:request('GET','/health')[0]==200)
                return process
            try:
                proc=start()
                credentials=dict(line.split(' : ') for line in (directory/'首次登录凭据.txt').read_text(encoding='utf-8').splitlines()[1:])
                st, me, hs=request('POST','/api/login',{'username':'admin','password':credentials['admin']})
                self.assertEqual(st,200)
                headers={'Cookie':hs['Set-Cookie'].split(';')[0],'X-CSRF-Token':me['csrf']}
                st, case, _=request('POST','/api/intake',{'url':'https://people.rednet.cn/front/messages/detail?id=991',
                    'phone':'19900000000','user_name':'进程恢复测试'},headers)
                self.assertEqual(st,201)
                wait_for(lambda:(directory/'collector-entered').exists())
                self.assertEqual(request('GET','/api/collection/jobs',headers=headers)[1]['items'][0]['state'],'running')
                proc.terminate();proc.wait(10);proc=None
                env['QUEUE_TEST_MODE']='finish'
                proc=start()
                wait_for(lambda:request('GET','/api/collection/jobs',headers=headers)[1]['counts']['completed']==1)
                tasks=request('GET','/api/collection/jobs',headers=headers)[1]
                self.assertEqual((tasks['total'],tasks['items'][0]['attempts']),(1,2))
                saved=request('GET','/api/cases/'+case['id'],headers=headers)[1]
                self.assertEqual((saved['version'],saved['evidence_count']),(2,1))
                self.assertEqual(request('GET','/api/cases',headers=headers)[1]['total'],1)
                self.assertTrue(request('GET','/api/audit',headers=headers)[1]['chain_valid'])
            finally:
                if proc:proc.terminate();proc.wait(10)
                log.close()


if __name__=='__main__':unittest.main()
