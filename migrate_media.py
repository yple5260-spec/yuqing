"""Offline local-media to S3 migration. Dry-run by default; retains source files."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet
from botocore.exceptions import ClientError
import database
import server
import storage


def migrate(apply=False):
    if not database.is_postgres():
        raise ValueError('附件迁移要求配置平台PostgreSQL')
    media = storage.Media(server.DATA)
    if not media.client:
        raise ValueError('附件迁移要求完整S3配置')
    key = os.environ.get('XIN_ENCRYPTION_KEY', '').encode() or (server.DATA / 'encryption.key').read_bytes()
    cipher = Fernet(key)
    server.acquire_instance_lock()
    try:
        with server.connect() as db:
            exists = db.execute("SELECT to_regclass('evidence_objects')").fetchone()[0]
            if apply:
                storage.ensure_schema(db)
                db.execute('LOCK TABLE evidence,evidence_objects IN ACCESS EXCLUSIVE MODE')
                exists = True
            rows = [dict(r) for r in db.execute('SELECT * FROM evidence ORDER BY id')]
            locations = {r['evidence_id']: dict(r) for r in db.execute('SELECT * FROM evidence_objects')} if exists else {}
            files = {p.name for p in media.directory.iterdir() if p.is_file()}
            if any(p.is_dir() for p in media.directory.iterdir()) or files - {r['filename'] for r in rows}:
                raise ValueError('media含未关联文件或子目录，请先核对，禁止遗漏迁移')
            plan = []
            for row in rows:
                raw = media.local_path(row['filename']).read_bytes()
                if hashlib.sha256(cipher.decrypt(raw)).hexdigest() != row['sha256']:
                    raise ValueError('本地证据摘要不一致，迁移中止')
                location = locations.get(row['id'])
                object_key = location['object_key'] if location else 'evidence/' + hashlib.sha256(row['id'].encode() + b'\0' + raw).hexdigest() + '.enc'
                if location and media.get(location['bucket'], object_key) != raw:
                    raise ValueError('现有S3引用与本地密文不同，禁止覆盖')
                plan.append({'evidence_id': row['id'], 'filename': row['filename'], 'bucket': media.bucket,
                             'object_key': object_key, 'cipher_sha256': hashlib.sha256(raw).hexdigest()})
            if not apply:
                return {'mode': 'dry-run', 'files': len(plan), 'already_migrated': len(locations), 'verified': True}
            # Journal survives a rolled-back DB transaction; deterministic keys make interrupted uploads resumable.
            journal = server.DATA / ('s3-migration-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
            with journal.open('x', encoding='utf-8') as stream:
                json.dump({'before': list(locations.values()), 'planned': plan}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(journal, 0o600)
            for item in plan:
                if item['evidence_id'] in locations:
                    continue
                raw = media.local_path(item['filename']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != item['cipher_sha256']:
                    raise ValueError('预检后本地文件发生变化，迁移回滚')
                media.put_verified(item['object_key'], raw)
                db.execute('INSERT INTO evidence_objects VALUES(?,?,?)',
                           (item['evidence_id'], item['bucket'], item['object_key']))
            if db.execute('SELECT count(*) FROM evidence_objects').fetchone()[0] != len(plan):
                raise ValueError('证据对象引用条数不一致，迁移回滚')
        return {'mode': 'applied', 'files': len(plan), 'verified': True, 'local_files': 'retained', 'journal': str(journal)}
    finally:
        server.INSTANCE_LOCK.close()
        server.DATABASE_LOCK.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.apply), ensure_ascii=False))
    except ClientError as exc:
        print('S3迁移失败，错误码：' + exc.response['Error']['Code'])
        raise SystemExit(1)
    except Exception as exc:
        print('S3迁移未完成：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
        raise SystemExit(1)
