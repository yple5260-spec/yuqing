"""Evidence S3 contract, HTTP authorization, and resumable migration checks."""
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from botocore.exceptions import ClientError
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import server
import storage
import test_deployment
from migrate_media import migrate

CONFIG = dict(zip(('TYAPP_S3_' + name for name in storage.S3_FIELDS),
                  ('https://s3.example.test', 'us-east-1', 'test-only', 'test-access', 'test-secret', 'true')))


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.fail_key = None

    def put_object(self, Bucket, Key, Body, ContentType, IfNoneMatch):
        assert IfNoneMatch == '*' and ContentType == 'application/octet-stream'
        if self.fail_key is not None and len(self.objects) >= self.fail_key:
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'PutObject')
        if (Bucket, Key) in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        self.objects[Bucket, Key] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': io.BytesIO(self.objects[Bucket, Key])}


class StorageTests(unittest.TestCase):
    def test_config_and_reference_fail_closed(self):
        fake = FakeS3()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CONFIG), patch('storage.boto3.client', return_value=fake) as factory:
            media = storage.Media(folder)
            args = factory.call_args.kwargs
            self.assertEqual(args['aws_access_key_id'], CONFIG['TYAPP_S3_ACCESS_KEY_ID'])
            self.assertEqual(args['aws_secret_access_key'], CONFIG['TYAPP_S3_SECRET_ACCESS_KEY'])
            self.assertEqual(args['endpoint_url'], CONFIG['TYAPP_S3_ENDPOINT'])
            self.assertEqual(args['region_name'], 'us-east-1')
            self.assertEqual(args['config'].signature_version, 's3v4')
            self.assertEqual(args['config'].s3['addressing_style'], 'path')
            media.put_verified('random.enc', b'encrypted')
            media.put_verified('random.enc', b'encrypted')
            with self.assertRaises(ValueError):
                media.put_verified('random.enc', b'different')
            with self.assertRaises(ValueError):
                media.get('other-bucket', 'random.enc')
            media.directory.mkdir()
            media.local_path('legacy.png').write_bytes(b'local copy must not mask S3 failure')
            db = Mock()
            db.execute.return_value.fetchone.return_value = {'bucket': media.bucket, 'object_key': 'missing.enc'}
            with self.assertRaises(ClientError):
                media.read(db, {'id': 'test', 'filename': 'legacy.png'})
            for name in ('../key', '..\\key', '/key'):
                with self.assertRaises(ValueError): media.local_path(name)
            with patch.dict(os.environ, {'TYAPP_S3_SECRET_ACCESS_KEY': ''}):
                with self.assertRaises(ValueError): storage.Media(folder)


class S3HTTPTests(test_deployment.DeploymentTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = FakeS3()
        with patch.dict(os.environ, CONFIG), patch('storage.boto3.client', return_value=cls.fake):
            server.MEDIA = storage.Media(server.DATA)

    def test_no_fallback_to_local_for_s3_reference(self):
        with server.connect() as db:
            with self.assertRaises(ClientError):
                server.MEDIA.get(server.MEDIA.bucket, 'missing.enc')
        self.assertEqual(list((server.DATA / 'media').iterdir()), [])


class S3MigrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('XIN_TEST_DATABASE_URL'), 'isolated PostgreSQL test connection not supplied')
    def test_migration_rollback_resume_and_unchanged_sources(self):
        url = os.environ['XIN_TEST_DATABASE_URL']
        schema = 'yuqing_s3_test_' + secrets.token_hex(6)
        original_data, original_cipher = server.DATA, getattr(server, 'CIPHER', None)
        with psycopg.connect(url) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {**CONFIG, 'TYAPP_DATABASE_URL': make_conninfo(url, options='-c search_path=' + schema), 'XIN_ENCRYPTION_KEY': ''}), patch('storage.boto3.client', return_value=FakeS3()) as factory:
                fake = factory.return_value
                server.DATA = Path(folder)
                server.init()
                with server.connect() as db:
                    cid = server.save_case(db, {'title': 'synthetic'}, {'username': 'admin'})
                    for index in range(2):
                        raw = ('synthetic-' + str(index)).encode()
                        filename = str(index) + '.png'
                        (server.DATA / 'media' / filename).write_bytes(server.CIPHER.encrypt(raw))
                        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)', (str(index), cid, 'https://example.test', '', server.digest(raw), filename, 'image/png', '', '', '', ''))
                original_files = {p.name: p.read_bytes() for p in (server.DATA / 'media').iterdir()}
                self.assertEqual(migrate()['files'], 2)
                self.assertEqual(fake.objects, {})
                fake.fail_key = 1
                with self.assertRaises(ClientError): migrate(True)
                with server.connect() as db:
                    self.assertEqual(db.execute('SELECT count(*) FROM evidence_objects').fetchone()[0], 0)
                self.assertEqual(len(fake.objects), 1)
                fake.fail_key = None
                self.assertTrue(migrate(True)['verified'])
                self.assertEqual(migrate()['already_migrated'], 2)
                self.assertTrue(migrate(True)['verified'])
                self.assertEqual(len(fake.objects), 2)
                self.assertEqual(original_files, {p.name: p.read_bytes() for p in (server.DATA / 'media').iterdir()})
                (server.DATA / 'media' / 'unreferenced').write_bytes(b'preserve me')
                with self.assertRaisesRegex(ValueError, '未关联'): migrate()
        finally:
            server.DATA, server.CIPHER = original_data, original_cipher
            with psycopg.connect(url) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__': unittest.main()
