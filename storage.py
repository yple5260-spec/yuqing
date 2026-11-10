"""Private encrypted evidence storage; database references select S3 or legacy files."""
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

S3_FIELDS = ('ENDPOINT', 'REGION', 'BUCKET', 'ACCESS_KEY_ID', 'SECRET_ACCESS_KEY', 'FORCE_PATH_STYLE')


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS evidence_objects(
        evidence_id TEXT PRIMARY KEY REFERENCES evidence(id),
        bucket TEXT NOT NULL, object_key TEXT NOT NULL,
        UNIQUE(bucket,object_key))''')


class Media:
    def __init__(self, data):
        self.directory = Path(data) / 'media'
        values = {name: os.environ.get('TYAPP_S3_' + name, '') for name in S3_FIELDS}
        self.client = None
        self.bucket = values['BUCKET']
        if not any(values.values()):
            return
        if not all(values.values()):
            raise ValueError('S3配置不完整，请检查六个TYAPP_S3_*变量')
        endpoint = urlsplit(values['ENDPOINT'])
        if (endpoint.scheme not in ('http', 'https') or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.path not in ('', '/')
                or endpoint.query or endpoint.fragment):
            raise ValueError('TYAPP_S3_ENDPOINT必须为不含路径或凭据的HTTP(S)地址')
        if values['REGION'] != 'us-east-1' or values['FORCE_PATH_STYLE'].lower() != 'true':
            raise ValueError('OneView S3要求us-east-1和Path-style')
        self.client = boto3.client(
            's3', endpoint_url=values['ENDPOINT'], region_name=values['REGION'],
            aws_access_key_id=values['ACCESS_KEY_ID'], aws_secret_access_key=values['SECRET_ACCESS_KEY'],
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                          connect_timeout=10, read_timeout=30,
                          retries={'mode': 'standard', 'max_attempts': 2}))

    def local_path(self, filename):
        if not filename or '/' in filename or '\\' in filename or filename in ('.', '..'):
            raise ValueError('证据文件名非法')
        path = self.directory / filename
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('证据路径越界')
        return path

    def get(self, bucket, key):
        if not self.client or bucket != self.bucket:
            raise ValueError('证据需要原S3存储配置，禁止跨桶读取')
        body = self.client.get_object(Bucket=bucket, Key=key)['Body']
        try:
            raw = body.read(40 * 1024 * 1024 + 1)
            if len(raw) > 40 * 1024 * 1024:
                raise ValueError('证据密文超过大小限制')
            return raw
        finally:
            body.close()

    def put_verified(self, key, raw):
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=raw,
                                   ContentType='application/octet-stream', IfNoneMatch='*')
        except ClientError as exc:
            if exc.response['Error']['Code'] not in ('PreconditionFailed', '412'):
                raise
        if self.get(self.bucket, key) != raw:
            raise ValueError('S3证据密文核对失败，禁止建立引用或覆盖对象')

    def write(self, db, evidence_id, filename, raw):
        if self.client:
            key = 'evidence/' + uuid4().hex + '.enc'
            # shortcut: DB rollback can leave an unreferenced object; retain it until reference-based cleanup exists.
            self.put_verified(key, raw)
            db.execute('INSERT INTO evidence_objects VALUES(?,?,?)', (evidence_id, self.bucket, key))
        else:
            self.local_path(filename).write_bytes(raw)

    def read(self, db, row):
        location = db.execute('SELECT bucket,object_key FROM evidence_objects WHERE evidence_id=?', (row['id'],)).fetchone()
        if location:
            return self.get(location['bucket'], location['object_key'])
        return self.local_path(row['filename']).read_bytes()
