"""Synthetic deployment checks. PostgreSQL always uses a disposable isolated schema."""
import base64
import http.client
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import threading
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import server
import routing
from migrate_sqlite import migrate


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = dict(os.environ)
        cls.url = os.environ.get('XIN_TEST_DATABASE_URL')
        if cls.url:
            cls.schema = 'yuqing_test_' + secrets.token_hex(6)
            with psycopg.connect(cls.url) as db:
                db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
            os.environ['TYAPP_DATABASE_URL'] = make_conninfo(cls.url, options='-c search_path=' + cls.schema)
        else:
            os.environ.pop('TYAPP_DATABASE_URL', None)
        os.environ.pop('XIN_ENCRYPTION_KEY', None)
        os.environ['XIN_SECURE_COOKIE'] = '1'
        os.environ['ONEVIEW_APP_SLUG'] = 'bfzx-yqda'
        cls.tmp = tempfile.TemporaryDirectory()
        server.DATA = Path(cls.tmp.name)
        server.init()
        cls.creds = dict(line.split(' : ') for line in (server.DATA / '首次登录凭据.txt').read_text(encoding='utf-8').splitlines()[1:])
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.tmp.cleanup()
        if cls.url:
            with psycopg.connect(cls.url) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))
        os.environ.clear()
        os.environ.update(cls.original)

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=15)
        conn.request(method, path, json.dumps(data).encode() if data is not None else None,
                     {'Content-Type': 'application/json', **(headers or {})})
        r = conn.getresponse()
        raw, hs, status = r.read(), dict(r.getheaders()), r.status
        conn.close()
        try: body = json.loads(raw)
        except ValueError: body = raw
        return status, body, hs

    def test_login_paths_cookies_roles_and_database(self):
        prefix = {'X-OneView-Route-Base': '/apps/bfzx-yqda'}
        for base in ('/', '/apps/bfzx-yqda/'):
            headers = {'X-OneView-Route-Base': base.rstrip('/') or '/'}
            st, raw, _ = self.request('GET', '/login', headers=headers)
            self.assertEqual(st, 200)
            self.assertIn(('<base href="' + base + '">').encode(), raw)
            self.assertNotIn(b'src="/static/', raw)
        self.assertEqual(self.request('GET', '/health')[0], 200)
        self.assertEqual(self.request('GET', '/api/cases')[0], 401)
        st, me, hs = self.request('POST', '/api/login', {'username': 'admin', 'password': self.creds['admin']}, prefix)
        self.assertEqual(st, 200)
        self.assertIn('Path=/apps/bfzx-yqda/', hs['Set-Cookie'])
        self.assertIn('; Secure', hs['Set-Cookie'])
        headers = {**prefix, 'Cookie': hs['Set-Cookie'].split(';')[0], 'X-CSRF-Token': me['csrf']}
        st, c, _ = self.request('POST', '/api/cases', {'event_code': 'TEST-ONLY', 'title': 'Synthetic deployment test', 'url': 'https://example.test/test', 'binding': 'pending', 'metrics': {}}, headers)
        self.assertEqual(st, 201, c)
        buf = io.BytesIO(); Image.new('RGB', (16, 16), 'white').save(buf, format='PNG')
        st, ev, _ = self.request('POST', '/api/cases/' + c['id'] + '/evidence', {'expected_version': c['version'], 'source_url': c['url'], 'captured_at': '2026-01-01T00:00:00+08:00', 'data': base64.b64encode(buf.getvalue()).decode(), 'note': 'synthetic fixture'}, headers)
        self.assertEqual(st, 201, ev)
        self.assertEqual(self.request('GET', '/api/evidence/' + ev['id'], headers=headers)[1], buf.getvalue())
        self.assertEqual(self.request('PUT', '/api/cases/' + c['id'], {'expected_version': 1}, headers)[0], 409)
        self.assertEqual(self.request('POST', '/api/exports', {'kind': 'masked', 'purpose': 'test only'}, headers)[0], 201)
        self.assertTrue(self.request('GET', '/api/audit', headers=headers)[1]['chain_valid'])
        st, _, hs = self.request('POST', '/api/logout', {}, headers)
        self.assertEqual(st, 200)
        self.assertIn('Path=/apps/bfzx-yqda/', hs['Set-Cookie'])
        self.assertEqual(self.request('GET', '/api/me', headers=headers)[0], 401)

    def test_proxy_validation(self):
        self.assertEqual(routing.route_base({'X-OneView-Route-Base': '/apps/bfzx-yqda'}, '8.8.8.8'), '/')
        self.assertEqual(self.request('GET', '/', headers={'X-OneView-Route-Base': '/evil'})[0], 400)

    def test_transaction_rollback_and_login_conflict(self):
        with self.assertRaises(RuntimeError):
            with server.connect() as db:
                db.execute('INSERT INTO login_limits VALUES(?,?,?)', ('synthetic', 1, 0))
                raise RuntimeError('rollback')
        with server.connect() as db:
            self.assertIsNone(db.execute('SELECT * FROM login_limits WHERE ip=?', ('synthetic',)).fetchone())
            q = 'INSERT INTO login_limits VALUES(?,?,?) ON CONFLICT(ip) DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until'
            db.execute(q, ('synthetic', 1, 0)); db.execute(q, ('synthetic', 2, 0))
            self.assertEqual(db.execute('SELECT failures FROM login_limits WHERE ip=?', ('synthetic',)).fetchone()[0], 2)


class MigrationTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('XIN_TEST_DATABASE_URL'), 'isolated PostgreSQL test connection not supplied')
    def test_migration_and_nonempty_refusal(self):
        original = dict(os.environ)
        schema = 'yuqing_migrate_' + secrets.token_hex(6)
        url = os.environ['XIN_TEST_DATABASE_URL']
        old_data = server.DATA
        with psycopg.connect(url) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                source, target = Path(tmp) / 'source', Path(tmp) / 'target'
                os.environ.pop('TYAPP_DATABASE_URL', None); os.environ.pop('XIN_ENCRYPTION_KEY', None)
                server.DATA = source; server.init()
                # Include an encrypted case, an image and a session that must not be migrated.
                with server.connect() as db:
                    actor = {'username': 'admin'}
                    c = server.normalize_case({'title': 'Synthetic source', 'event_code': 'TEST', 'url': 'https://example.test/source', 'binding': 'pending', 'metrics': {}}, actor)
                    cid = server.save_case(db, c, actor)
                    raw = b'synthetic encrypted attachment'
                    (source / 'media' / 'test.png').write_bytes(server.CIPHER.encrypt(raw))
                    db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('TEST-EV', cid, c['url'], '2026-01-01T00:00:00+08:00', server.digest(raw), 'test.png', 'image/png', server.now(), 'manual_upload', '', 'synthetic'))
                    db.execute('INSERT INTO sessions VALUES(?,?,?,?)', ('test', 'admin', 'test', 9999999999))
                os.environ['TYAPP_DATABASE_URL'] = make_conninfo(url, options='-c search_path=' + schema)
                server.DATA = target
                self.assertEqual(migrate(source)['mode'], 'dry-run')
                self.assertFalse(target.exists())
                result = migrate(source, True)
                self.assertEqual(result['counts']['cases'], 1)
                self.assertTrue((source / 'xin.db').exists())
                self.assertTrue((target / 'pre-migration-backup' / 'xin.db').exists())
                self.assertEqual(server.CIPHER.decrypt((target / 'media' / 'test.png').read_bytes()), raw)
                with server.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
                    self.assertEqual(server.dec(db.execute('SELECT body FROM cases').fetchone()[0])['title'], 'Synthetic source')
                    server.audit(db, 'test', 'post migration')
                with self.assertRaisesRegex(ValueError, '拒绝覆盖'):
                    migrate(source, True)
        finally:
            server.DATA = old_data
            os.environ.clear(); os.environ.update(original)
            with psycopg.connect(url) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__': unittest.main()
