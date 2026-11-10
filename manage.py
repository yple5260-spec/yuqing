#!/usr/bin/env python3
"""Stopped-instance backup and integrity checks. Backups contain the local key: protect them."""
import argparse,hashlib,json,os,sqlite3,tempfile,zipfile
from pathlib import Path
import server
p=argparse.ArgumentParser(description='换芯计划运维工具；备份前停止服务。')
p.add_argument('command',choices=['backup','verify-backup']);p.add_argument('file',type=Path);a=p.parse_args()
if a.command=='backup':
 if server.database.is_postgres():raise SystemExit('PostgreSQL 请使用 pg_dump 备份，并另行备份 media 和原加密密钥；本命令仅支持 SQLite。')
 if not (server.DATA/'xin.db').exists():raise SystemExit('数据目录中没有数据库。')
 server.acquire_instance_lock()
 if a.file.exists():raise SystemExit('目标文件已存在，拒绝覆盖。')
 a.file.parent.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory() as td:
  snapshot=Path(td)/'xin.db'
  with sqlite3.connect(server.DATA/'xin.db') as src,sqlite3.connect(snapshot) as dst:src.backup(dst)
  dst.close();src.close()
  db=sqlite3.connect(snapshot);db.execute('DELETE FROM sessions');db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
  sources=[('xin.db',snapshot)]+[(str(x.relative_to(server.DATA)),x) for x in server.DATA.rglob('*') if x.is_file() and (x.name=='encryption.key' or x.is_relative_to(server.DATA/'media'))]
  manifest={}
  with zipfile.ZipFile(a.file,'w',zipfile.ZIP_DEFLATED) as z:
   for name,path in sources:
    raw=path.read_bytes();z.writestr(name,raw);manifest[name]=hashlib.sha256(raw).hexdigest()
   z.writestr('backup-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
 os.chmod(a.file,0o600)
 print('备份完成：'+str(a.file.resolve())+'\n注意：备份含本地加密密钥，须放入受控加密介质；环境变量提供的密钥须另行备份。')
else:
 with zipfile.ZipFile(a.file) as z:
  manifest=json.loads(z.read('backup-manifest.json'))
  for name,expected in manifest.items():
   if hashlib.sha256(z.read(name)).hexdigest()!=expected:raise SystemExit('备份校验失败：'+name)
 print('备份文件摘要校验通过，共 '+str(len(manifest))+' 项。此校验不代替恢复演练。')
