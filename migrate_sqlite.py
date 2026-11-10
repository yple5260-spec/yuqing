"""Offline migration. Default is read-only validation; --apply requires empty target tables."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from cryptography.fernet import Fernet
import database

TABLES = ('accounts', 'rules', 'cases', 'history', 'evidence', 'reviews', 'overrides', 'audit', 'exports', 'imports')


def migrate(source, apply=False):
    if not database.is_postgres():
        raise ValueError('必须配置 TYAPP_DATABASE_URL；不会回退写 SQLite')
    source = Path(source).resolve()
    dbfile = source / 'xin.db'
    if not dbfile.is_file():
        raise ValueError('源目录缺少 xin.db')
    key = os.environ.get('XIN_ENCRYPTION_KEY', '').encode()
    if not key:
        key = (source / 'encryption.key').read_bytes()
    cipher = Fernet(key)
    import server
    # The same OS lock as the application enforces a stopped local source; DB lock excludes target instances.
    target_data = server.DATA
    if target_data == source:
        raise ValueError('迁移目标文件目录必须与源目录分开')
    server.DATA = source
    try:
        server.acquire_instance_lock()
    finally:
        server.DATA = target_data
    src = sqlite3.connect(dbfile.as_uri() + '?mode=ro', uri=True)
    src.row_factory = sqlite3.Row
    try:
        src.execute('BEGIN')
        if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('源数据库完整性检查失败')
        rows = {t: [dict(r) for r in src.execute('SELECT * FROM ' + t)] for t in TABLES}
        for table in ('cases', 'history', 'exports', 'imports'):
            for row in rows[table]:
                raw = cipher.decrypt(row['body'].encode())
                if table != 'exports':
                    json.loads(raw)
                elif hashlib.sha256(raw).hexdigest() != row['sha256']:
                    raise ValueError('导出文件摘要不一致')
        prev = '0' * 64
        for row in sorted(rows['audit'], key=lambda r: r['id']):
            fields = [row[k] for k in ('time', 'actor', 'action', 'target', 'detail', 'prev')]
            if row['prev'] != prev or server.digest(server.js(fields).encode()) != row['hash']:
                raise ValueError('源审计链校验失败')
            prev = row['hash']
        for row in rows['evidence']:
            name = row['filename']
            if Path(name).name != name:
                raise ValueError('证据文件名非法')
            raw = cipher.decrypt((source / 'media' / name).read_bytes())
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('证据文件摘要不一致')
        with database.connect(target_data) as target:
            for table in TABLES + ('sessions', 'login_limits'):
                exists = target.execute('SELECT to_regclass(?)', (table,)).fetchone()[0]
                if exists and target.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]:
                    raise ValueError('目标库已有应用数据，拒绝覆盖或合并')
        counts = {t: len(r) for t, r in rows.items()}
        if not apply:
            return {'mode': 'dry-run', 'counts': counts, 'verified': True}
        target_data.mkdir(parents=True, exist_ok=True)
        kp = target_data / 'encryption.key'
        if kp.exists() and kp.read_bytes() != key:
            raise ValueError('目标目录密钥与原库不同，拒绝覆盖')
        media = target_data / 'media'
        media.mkdir(exist_ok=True)
        for row in rows['evidence']:
            dest = media / row['filename']
            raw = (source / 'media' / row['filename']).read_bytes()
            if dest.exists() and dest.read_bytes() != raw:
                raise ValueError('目标存在不同的同名附件，拒绝覆盖')
        # Keep an unchanged SQLite snapshot plus files/key before the first target write.
        backup = target_data / 'pre-migration-backup'
        backup.mkdir(exist_ok=False)
        os.chmod(backup, 0o700)
        snapshot = sqlite3.connect(backup / 'xin.db')
        try:
            src.backup(snapshot)
        finally:
            snapshot.close()
        (backup / 'encryption.key').write_bytes(key)
        shutil.copytree(source / 'media', backup / 'media')
        os.chmod(backup / 'encryption.key', 0o600)
        kp.write_bytes(key)
        os.chmod(kp, 0o600)
        os.environ['XIN_ENCRYPTION_KEY'] = key.decode()
        server.init(create_accounts=False, create_rules=False)
        with database.connect(target_data) as target:
            # Recheck under table locks so another writer cannot race the empty-target check.
            target.execute('LOCK TABLE ' + ','.join(TABLES + ('sessions', 'login_limits')) + ' IN ACCESS EXCLUSIVE MODE')
            for table in TABLES + ('sessions', 'login_limits'):
                if target.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]:
                    raise ValueError('目标数据在预检后变化，迁移已回滚')
            for table in TABLES:
                for row in rows[table]:
                    columns = ','.join(row)
                    target.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
                if target.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] != counts[table]:
                    raise ValueError('迁移条数不一致')
                actual=[dict(r) for r in target.execute('SELECT * FROM '+table)]
                canonical=lambda records: sorted(json.dumps(r,sort_keys=True,ensure_ascii=False) for r in records)
                if canonical(actual)!=canonical(rows[table]):raise ValueError('迁移内容校验不一致')
            for table in ('history', 'reviews', 'audit'):
                target.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE(MAX(id),1), COUNT(*)>0) FROM {table}")
            for row in rows['evidence']:
                shutil.copy2(source / 'media' / row['filename'], media / row['filename'])
        return {'mode': 'applied', 'counts': counts, 'verified': True, 'sessions': 'not migrated'}
    finally:
        src.close()
        server.INSTANCE_LOCK.close()
        if hasattr(server, 'DATABASE_LOCK'):
            server.DATABASE_LOCK.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.source, args.apply), ensure_ascii=False))
    except Exception as exc:
        # Connection errors may include private infrastructure details.
        print('迁移未完成：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
        raise SystemExit(1)
