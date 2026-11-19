"""Synthetic HTTP checks. No production database or public website is contacted."""
import base64
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import collection
import server


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = patch.dict(os.environ, {'TYAPP_DATABASE_URL': '', 'XIN_ENCRYPTION_KEY': '',
            'XIN_ALLOWED_HOSTS': '127.0.0.1', **{'TYAPP_S3_' + x: '' for x in server.storage.S3_FIELDS}})
        cls.env.start()
        cls.old_data = server.DATA
        server.DATA = Path(cls.tmp.name)
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        credentials = dict(x.split(' : ') for x in (server.DATA / '首次登录凭据.txt').read_text(encoding='utf-8').splitlines()[1:])
        cls.sessions = {}
        for name in ('admin', 'reviewer'):
            status, me, hs = cls.request('POST', '/api/login', {'username': name, 'password': credentials[name]})
            assert status == 200
            cls.sessions[name] = {'Cookie': hs['Set-Cookie'].split(';')[0], 'X-CSRF-Token': me['csrf']}
        buf = io.BytesIO()
        Image.new('RGB', (10, 10), 'white').save(buf, format='PNG')
        cls.image = buf.getvalue()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()
        cls.tmp.cleanup(); server.DATA = cls.old_data; cls.env.stop()

    @classmethod
    def request(cls, method, path, body=None, who=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', cls.http.server_port, timeout=5)
        c.request(method, path, json.dumps(body).encode() if body is not None else None,
                  {'Content-Type': 'application/json', **cls.sessions.get(who, {}), **(headers or {})})
        r = c.getresponse(); raw = r.read(); status = r.status; hs = dict(r.getheaders()); c.close()
        try: raw = json.loads(raw)
        except (ValueError, UnicodeError): pass
        return status, raw, hs

    def collect_now(self, method, endpoint, body, who):
        st, task, hs = self.request(method, endpoint, body, who)
        if st != 202: return st, task, hs
        worker = server.jobs.Worker(server)
        for _ in range(30):
            with server.connect() as db:
                row = db.execute('SELECT * FROM collection_jobs WHERE id=?', (task['id'],)).fetchone()
                item = server.jobs.view(row, server)
            if item['state'] not in server.jobs.ACTIVE:
                if item['state'] in ('completed', 'partial'):return 201, item['result'], hs
                if item['state'] == 'restricted':return 200, {'status': 'restricted', 'message': item['message']}, hs
                return 409, item, hs
            worker.run_once()
        raise AssertionError('queue did not settle')

    def case(self, number):
        st, c, _ = self.request('POST', '/api/cases', {'event_code': 'TEST-' + str(number),
            'title': 'Synthetic fixture', 'url': 'https://people.rednet.cn/front/messages/detail?id=' + str(number),
            'binding': 'pending', 'metrics': {}}, 'admin')
        self.assertEqual(st, 201)
        return c, '/api/cases/' + c['id']

    def result(self, url):
        return {'status': 'ok', 'platform': '红网·百姓呼声', 'title': 'Captured fixture title',
            'source_url': url, 'nickname': 'Synthetic nickname', 'content': 'synthetic-public-content',
            'published_at': '2026-01-05', 'captured_at': server.now(), 'engine': 'crawl4ai-0.9.5',
            'fans': None, 'posts7': None, 'posts_per_day7': None, 'metrics_note': 'Unavailable metrics',
            'data': base64.b64encode(self.image).decode()}


class CollectionTests(TestServer):
    def test_new_channels_intake_queue_portrait_and_alias_dedup(self):
        for url, platform, uid, fans in (
                ('https://www.toutiao.com/w/1853479850731843/', '今日头条', '61276690339', 10),
                ('https://www.bilibili.com/video/BV1JcifBWEad/', '哔哩哔哩', '3546928298068210', None),
                ('https://www.douyin.com/note/7591486008592990714', '抖音', '7583632343584343097', 14)):
            with self.subTest(platform=platform):
                body = {'url': url, 'phone': '19900000000', 'user_name': '新渠道隔离测试'}
                st, case, _ = self.request('POST', '/api/intake', body, 'admin')
                self.assertEqual(st, 201);self.assertEqual(case['platform'], platform)
                self.assertEqual(self.request('POST', '/api/intake', {**body, 'url': url + '?share=tracking'}, 'admin')[0], 422)
                report = self.result(url)
                report.update(platform=platform, collection_state='partial', uid=uid, fans=fans,
                              published_at='2026-01-05T20:28:49+08:00', published_precision='second',
                              published_timezone='Asia/Shanghai', profile_captured_at=report['captured_at'])
                report['interactions'] = {'share': 0, 'comment': 2, 'like': 1}
                report['structured_evidence'] = [collection.weibo.structured_evidence(url, report['captured_at'], {'uid': uid, 'fans': fans})]
                endpoint = '/api/cases/' + case['id']
                with patch.object(collection, 'collect', return_value=report):
                    st, result, _ = self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')
                self.assertEqual(st, 201);self.assertEqual(result['collection_state'], 'partial')
                _, saved, _ = self.request('GET', endpoint, who='admin')
                self.assertEqual(saved['collection_task']['state'], 'partial')
                fields = {x['key']: x for x in saved['portrait']['fields']}
                self.assertEqual(fields['uid']['value'], uid);self.assertEqual(fields['fans']['value'], fans)
                self.assertEqual(fields['fans']['source_url'], url)
                self.assertEqual(fields['share']['label'], '累计分享');self.assertEqual(fields['share']['value'], 0)
                self.assertNotIn('repost', fields)
                self.assertIsNone(saved['portrait']['activity7']['count'])
                self.assertEqual(saved['binding'], 'pending')
                self.assertTrue(all(x['value'] is None for x in saved['metrics'].values()))
                self.assertEqual(saved['evidence_count'], 1)

    def test_url_boundary(self):
        self.assertEqual(len(collection.PLATFORMS), 3)
        for url in ('http://people.rednet.cn/front/messages/detail?id=1', 'https://127.0.0.1/a',
                    'https://people.rednet.cn.evil.test/front/messages/detail?id=1',
                    'https://people.rednet.cn/front/messages/detail?id=1&redirect=http://localhost',
                    'https://zw.enorth.com.cn:444/gov_open/1.html',
                    'https://user@ly.scol.com.cn/thread/details/1', 'https://ly.scol.com.cn/thread/details/1#x'):
            with self.subTest(url=url), self.assertRaises(ValueError): collection.platform_for(url)

    def test_three_field_intake(self):
        body = {'url': 'https://people.rednet.cn/front/messages/detail?id=700',
                'phone': '19900000000', 'user_name': '隔离测试姓名'}
        self.assertEqual(self.request('POST', '/api/intake', body)[0], 401)
        self.assertEqual(self.request('POST', '/api/intake', body, 'reviewer')[0], 403)
        for invalid in ({**body, 'phone': ''}, {**body, 'phone': 'invalid'},
                        {**body, 'user_name': ' '}, {**body, 'user_name': '名' * 101},
                        {**body, 'url': 'javascript:alert(1)'}):
            self.assertEqual(self.request('POST', '/api/intake', invalid, 'admin')[0], 422)
        st, c, _ = self.request('POST', '/api/intake', {**body, 'binding': 'verified', 'metrics': {'fans': 99}}, 'admin')
        self.assertEqual(st, 201, c)
        self.assertEqual(c['user_name'], body['user_name'])
        self.assertTrue(c['event_code'].startswith('EV-'))
        self.assertEqual(c['platform'], '红网·百姓呼声')
        self.assertEqual(c['binding'], 'pending')
        self.assertTrue(all(m['value'] is None for m in c['metrics'].values()))
        self.assertEqual(self.request('POST', '/api/intake', body, 'admin')[0], 422)
        endpoint = '/api/cases/' + c['id']
        with patch.object(collection, 'collect', return_value=self.result(c['url'])):
            self.assertEqual(self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')[0], 201)
        _, saved, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(saved['user_name'], body['user_name'])
        self.assertNotIn(body['user_name'].encode(), (server.DATA / 'xin.db').read_bytes())
        st, edited, _ = self.request('PUT', endpoint, {'expected_version': 2, 'quality_note': 'edit'}, 'admin')
        self.assertEqual(st, 200)
        self.assertEqual(edited['user_name'], body['user_name'])

    def test_capture_encrypted_versioned_and_editor_preserves_result(self):
        c, endpoint = self.case(1)
        with patch.object(collection, 'collect', return_value=self.result(c['url'])):
            st, ev, _ = self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')
        self.assertEqual(st, 201, ev)
        _, c, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(c['version'], 2)
        self.assertEqual(c['collection']['nickname'], 'Synthetic nickname')
        self.assertNotIn('data', c['collection'])
        self.assertIsNone(c['collection']['fans'])
        self.assertTrue(all(m['value'] is None for m in c['metrics'].values()))
        self.assertEqual(c['binding'], 'pending')
        self.assertEqual(c['evidence'][0]['method'], 'browser_capture')
        st, raw, _ = self.request('GET', '/api/evidence/' + ev['id'], who='admin')
        self.assertEqual(raw, self.image)
        self.assertNotEqual((server.DATA / 'media' / (ev['id'] + '.png')).read_bytes(), self.image)
        self.assertNotIn(b'synthetic-public-content', (server.DATA / 'xin.db').read_bytes())
        st, c, _ = self.request('PUT', endpoint, {'expected_version': 2, 'quality_note': 'updated',
            'collection': {'nickname': 'forged'}}, 'admin')
        self.assertEqual(st, 200)
        self.assertEqual(c['collection']['nickname'], 'Synthetic nickname')

    def test_permissions_challenge_and_no_lock_during_network(self):
        c, endpoint = self.case(2)
        with patch.object(collection, 'collect') as run:
            self.assertEqual(self.request('POST', endpoint + '/collect', {'expected_version': 1})[0], 401)
            self.assertEqual(self.request('POST', endpoint + '/collect', {'expected_version': 1}, 'reviewer')[0], 403)
            self.assertEqual(self.request('POST', endpoint + '/collect', {'expected_version': 1}, 'admin', {'X-CSRF-Token': ''})[0], 403)
            run.assert_not_called()
        with patch.object(collection, 'collect', return_value={'status': 'restricted', 'message': 'restricted'}):
            self.assertEqual(self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')[1]['status'], 'restricted')
        _, before, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual((before['version'], before['evidence_count']), (1, 0))
        def concurrent_edit(url):
            st, _, _ = self.request('PUT', endpoint, {'expected_version': 1, 'quality_note': 'concurrent edit'}, 'admin')
            self.assertEqual(st, 200)
            return self.result(url)
        with patch.object(collection, 'collect', side_effect=concurrent_edit):
            self.assertEqual(self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')[0], 409)
        _, after, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual((after['version'], after['evidence_count']), (2, 0))
        self.assertEqual(after['quality_note'], 'concurrent edit')
        self.assertTrue(self.request('GET', '/api/audit', who='admin')[1]['chain_valid'])

    def test_weibo_partial_capture_alias_conflict_and_no_auto_scoring(self):
        body = {'url': 'https://weibo.com/2/detail/5364128086494866',
                'phone': '19900000000', 'user_name': '微博隔离测试'}
        st, c, _ = self.request('POST', '/api/intake', body, 'admin')
        self.assertEqual(st, 201)
        self.assertEqual(c['platform'], '微博')
        self.assertEqual(self.request('POST', '/api/intake', {**body,
            'url': 'https://m.weibo.cn/detail/5364128086494866'}, 'admin')[0], 422)
        report = self.result(body['url'])
        report.update(platform='微博', collection_state='partial', fans=163445, published_at='',
                      published_display='11-11 18:00', posts_per_day7=None,
                      structured_evidence=[collection.weibo.structured_evidence(body['url'], server.now(), {'fans': 163445})])
        endpoint = '/api/cases/' + c['id']
        with patch.object(collection, 'collect', return_value=report):
            st, result, _ = self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')
        self.assertEqual(st, 201, result)
        self.assertEqual(result['collection_state'], 'partial')
        _, saved, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(saved['published_at'], '')
        self.assertEqual(saved['collection']['fans'], 163445)
        self.assertEqual(len(saved['collection']['structured_evidence']), 1)
        self.assertTrue(all(m['value'] is None for m in saved['metrics'].values()))
        self.assertEqual(saved['binding'], 'pending')
        self.request('PUT', endpoint, {'expected_version': 2, 'published_at': '2026-11-11T18:00:00+08:00'}, 'admin')
        refreshed = self.result(body['url'])
        refreshed.update(platform='微博', collection_state='partial', fans=163446, published_at='')
        with patch.object(collection, 'collect', return_value=refreshed):
            st, reused, _ = self.collect_now('POST', endpoint + '/collect', {'expected_version': 3}, 'admin')
        self.assertEqual(st, 201, reused)
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['id'], result['id'])
        _, updated, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(updated['collection']['fans'], 163446)
        self.assertEqual(updated['published_at'], '2026-11-11T18:00:00+08:00')
        self.assertEqual(updated['evidence_count'], 1)


if __name__ == '__main__': unittest.main()
