"""Convenience launcher. Never installs packages or downloads anything implicitly."""
import importlib.util,os,sys,urllib.request,webbrowser
from pathlib import Path
if sys.version_info<(3,11):raise SystemExit('请使用 Python 3.11 或以上版本。macOS 自带的旧版 Python 通常不符合要求。')
ROOT=Path(__file__).resolve().parent;os.chdir(ROOT)
missing=[p for p in ('cryptography','PIL') if importlib.util.find_spec(p) is None]
if missing:
 print('缺少依赖：'+','.join(missing)+'\n请由管理员在本目录执行以下命令（首次安装需要联网，或使用离线 wheel 仓库）：')
 print('"'+sys.executable+'" -m pip install -r requirements.txt')
 raise SystemExit(1)
port=int(os.environ.get('XIN_PORT','8765'));url='http://127.0.0.1:'+str(port)
try:
 import json
 with urllib.request.urlopen(url+'/health',timeout=1) as r:healthy=json.load(r).get('version')=='1.0-pilot'
except Exception:healthy=False
if healthy:
 print('系统已在运行：'+url+'\n首次密码文件：'+str(ROOT/'data/首次登录凭据.txt'));webbrowser.open(url);raise SystemExit()
print('系统地址：'+url+'\n启动后请在浏览器打开上述地址。保持此终端窗口运行；按 Ctrl+C 停止。',flush=True)
# Launch the browser only once the local service is accepting connections.
import threading,time
def reveal():
 for _ in range(100):
  try:
   with urllib.request.urlopen(url+'/health',timeout=.5) as r:
    if r.status==200:webbrowser.open(url);return
  except Exception:time.sleep(.1)
threading.Thread(target=reveal,daemon=True).start()
import server
sys.argv=[str(ROOT/'server.py')];server.main()
