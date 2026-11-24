"""Durable task and batch checks use an isolated database and synthetic collectors."""
import base64
import io
import json
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_collection import TestServer
import server
import jobs


class JobsTests(TestServer):
    def setUp(self):
        with server.connect() as db:
            db.execute('DELETE FROM collection_jobs')

    def intake(self, url, name='隔离批量测试'):
        st, c, _ = self.request('POST', '/api/intake', {'url': url, 'phone': '19900000000', 'user_name': name}, 'admin')
        self.assertEqual(st, 201, c)
        return c

    def test_mixed_batch_preview_commit_and_terminal_results(self):
        self.case(800)
        text = '''舆情原文链接,手机号,用户姓名
https://people.rednet.cn/front/messages/detail?id=801,19900000000,批量测试甲
https://ly.scol.com.cn/thread/details/802,19900000000,批量测试乙
https://weibo.com/2/detail/5364128086494866,19900000000,批量测试丙
https://m.weibo.cn/detail/5364128086494866,19900000000,同批别名重复
https://people.rednet.cn/front/messages/detail?id=803,bad,手机号错误
https://example.test/unsupported,19900000000,不支持平台
https://people.rednet.cn/front/messages/detail?id=800,19900000000,库内重复
'''
        with server.connect() as db: before = db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]
        st, preview, _ = self.request('POST', '/api/import/preview', {'format': 'csv', 'mode': 'three', 'text': text}, 'admin')
        self.assertEqual(st, 200, preview)
        self.assertEqual((preview['valid'], len(preview['duplicates']), len(preview['errors'])), (4, 2, 1))
        self.assertEqual(preview['errors'][0]['row'], 6)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cases').fetchone()[0], before)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM collection_jobs').fetchone()[0], 0)
        st, committed, _ = self.request('POST', '/api/import/commit', {'id': preview['id']}, 'admin')
        self.assertEqual(st, 201, committed)
        self.assertEqual(len(committed['created']), 4)
        self.assertEqual(self.request('POST', '/api/import/commit', {'id': preview['id']}, 'admin')[0], 422)
        self.assertEqual(self.request('GET', '/api/collection/jobs')[0], 401)
        def collect(url):
            if 'scol' in url:return {'status': 'restricted', 'message': '平台访问受限，不要求人工验证码'}
            report = self.result(url)
            if 'weibo.com' in url:report.update(platform='微博', collection_state='partial', fans=100)
            return report
        worker = jobs.Worker(server)
        with patch.object(server.collection, 'collect', side_effect=collect) as run:
            while worker.run_once():pass
            self.assertEqual(run.call_count, 3)
        _, report, _ = self.request('GET', '/api/collection/jobs?batch_id=' + committed['batch_id'], who='admin')
        self.assertEqual(report['counts'], {'queued': 0, 'running': 0, 'completed': 1, 'partial': 1, 'restricted': 1, 'failed': 1})
        self.assertEqual(len(report['items']), 4)
        limited = next(t for t in report['items'] if t['state'] == 'restricted')
        endpoint = '/api/collection/jobs/' + limited['id'] + '/retry'
        self.assertEqual(self.request('POST', endpoint, {}, 'reviewer')[0], 403)
        self.assertEqual(self.request('POST', endpoint, {}, 'admin', {'X-CSRF-Token': ''})[0], 403)
        self.assertEqual(self.request('POST', endpoint, {}, 'admin')[0], 202)
        self.assertEqual(self.request('POST', endpoint, {}, 'admin')[0], 422)
        with patch.object(server.collection, 'collect', side_effect=collect): self.assertTrue(worker.run_once())
        self.assertEqual(self.request('GET', '/api/collection/jobs?state=restricted', who='reviewer')[0], 403)
        _, filtered, _ = self.request('GET', '/api/collection/jobs?state=restricted', who='admin')
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['items'][0]['attempts'], 2)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM evidence WHERE case_id IN (?,?,?,?)', committed['created']).fetchone()[0], 2)
        self.assertNotIn('批量测试甲'.encode(), (server.DATA / 'xin.db').read_bytes())
        self.assertTrue(self.request('GET', '/api/audit', who='admin')[1]['chain_valid'])

    def test_evidence_and_collection_visibility_by_role(self):
        case = self.intake('https://people.rednet.cn/front/messages/detail?id=970')
        capture = self.result(case['url'])
        capture['collection_state'] = 'partial'
        with patch.object(server.collection, 'collect', return_value=capture):
            self.assertTrue(jobs.Worker(server).run_once())

        endpoint = '/api/cases/' + case['id']
        _, full_case, _ = self.request('GET', endpoint, who='admin')
        evidence = full_case['evidence'][0]
        image_path = '/api/evidence/' + evidence['id']
        tasks_path = '/api/collection/jobs'
        retry_path = tasks_path + '/' + case['task']['id'] + '/retry'
        self.assertEqual(self.request('GET', image_path)[0], 401)
        self.assertEqual(self.request('GET', tasks_path)[0], 401)

        for role in ('admin', 'analyst', 'reviewer', 'exporter', 'viewer'):
            with self.subTest(role=role):
                username = 'test-only-' + role
                password = secrets.token_urlsafe(16)
                status, _, _ = self.request('POST', '/api/accounts', {
                    'username': username, 'password': password, 'roles': [role]}, 'admin')
                self.assertEqual(status, 201)
                status, actor, headers = self.request('POST', '/api/login', {'username': username, 'password': password})
                self.assertEqual(status, 200)
                self.sessions[username] = {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-CSRF-Token': actor['csrf']}

                operator = role in ('admin', 'analyst')
                original = operator or role == 'reviewer'
                status, raw, _ = self.request('GET', image_path, who=username)
                self.assertEqual(status, 200 if original else 403)
                if original:
                    self.assertEqual(raw, self.image)
                status, task_list, _ = self.request('GET', tasks_path, who=username)
                self.assertEqual(status, 200 if operator else 403)
                if operator:
                    self.assertEqual(task_list['items'][0]['url'], case['url'])
                else:
                    self.assertEqual(self.request('POST', retry_path, {}, username)[0], 403)
                status, dossier, _ = self.request('GET', endpoint, who=username)
                self.assertEqual(status, 200)
                self.assertEqual(dossier['collection_task']['state'], 'partial')
                self.assertEqual(dossier['collection']['nickname'], capture['nickname'])
                if operator:
                    self.assertEqual(dossier['collection_task']['result']['id'], evidence['id'])
                else:
                    self.assertEqual(set(dossier['collection_task']), {'state', 'stage', 'updated_at'})
                expected_fields = set(evidence) if original else {'id', 'source_url', 'captured_at', 'source_cell', 'sha256'}
                self.assertEqual(set(dossier['evidence'][0]), expected_fields)
                self.assertEqual(dossier['evidence'][0]['sha256'], evidence['sha256'])

    def test_restart_recovery_version_protection_and_single_active_job(self):
        c = self.intake('https://people.rednet.cn/front/messages/detail?id=900')
        endpoint = '/api/cases/' + c['id'] + '/collect'
        st, repeated, _ = self.request('POST', endpoint, {'expected_version': 1}, 'admin')
        self.assertEqual((st, repeated['id']), (202, c['task']['id']))
        with server.connect() as db:
            db.execute("UPDATE collection_jobs SET state='running',attempts=1 WHERE id=?", (c['task']['id'],))
        worker = jobs.Worker(server)
        worker.recover()
        with server.connect() as db:self.assertEqual(db.execute('SELECT state FROM collection_jobs').fetchone()[0], 'queued')
        with patch.object(server.collection, 'collect', return_value=self.result(c['url'])):
            worker.run_once()
        with server.connect() as db:
            row = db.execute('SELECT * FROM collection_jobs').fetchone()
            self.assertEqual((row['state'], row['attempts']), ('completed', 2))
        # Pending jobs do not overwrite an intervening edit after restart.
        changed = self.intake('https://people.rednet.cn/front/messages/detail?id=901')
        self.request('PUT', '/api/cases/' + changed['id'], {'expected_version': 1, 'quality_note': 'must retain'}, 'admin')
        with patch.object(server.collection, 'collect') as collect:
            worker.run_once(); collect.assert_not_called()
        _, saved, _ = self.request('GET', '/api/cases/' + changed['id'], who='admin')
        self.assertEqual(saved['quality_note'], 'must retain')
        self.assertEqual(saved['collection_task']['state'], 'failed')

    def test_precommit_race_and_permission_revocation(self):
        text = '舆情原文链接,手机号,用户姓名\nhttps://people.rednet.cn/front/messages/detail?id=910,19900000000,抢先建档测试\n'
        _, preview, _ = self.request('POST', '/api/import/preview', {'format': 'csv', 'mode': 'three', 'text': text}, 'admin')
        c = self.intake('https://people.rednet.cn/front/messages/detail?id=910')
        st, committed, _ = self.request('POST', '/api/import/commit', {'id': preview['id']}, 'admin')
        self.assertEqual(st, 201)
        self.assertEqual((len(committed['created']), len(committed['skipped'])), (0, 1))
        with server.connect() as db:db.execute('UPDATE accounts SET active=0 WHERE username=?', ('admin',))
        try:
            with patch.object(server.collection, 'collect') as collect:
                jobs.Worker(server).run_once(); collect.assert_not_called()
            with server.connect() as db:
                row = db.execute('SELECT * FROM collection_jobs WHERE id=?', (c['task']['id'],)).fetchone()
                self.assertEqual(row['state'], 'failed')
        finally:
            with server.connect() as db:db.execute('UPDATE accounts SET active=1 WHERE username=?', ('admin',))

    def test_three_field_xlsx_and_csv_headers(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="导入" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            rows = [('舆情原文链接','手机号','用户姓名'),('https://people.rednet.cn/front/messages/detail?id=920','19900000000','工作簿测试')]
            xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            for n, values in enumerate(rows, 1):
                xml += '<row r="'+str(n)+'">'+''.join('<c r="'+col+str(n)+'" t="inlineStr"><is><t>'+value+'</t></is></c>' for col,value in zip('ABC', values))+'</row>'
            z.writestr('xl/worksheets/sheet1.xml', xml+'</sheetData></worksheet>')
        st, preview, _ = self.request('POST', '/api/import/preview', {'format': 'xlsx_three', 'data': base64.b64encode(buf.getvalue()).decode()}, 'admin')
        self.assertEqual(st, 200, preview)
        self.assertEqual(preview['valid'], 1)
        self.assertEqual(preview['preview'][0]['user_name'], '工作簿测试')
        self.assertEqual(self.request('POST', '/api/import/preview', {'format': 'csv', 'mode': 'three', 'text': 'foo,bar\nx,y'}, 'admin')[0], 422)

    def test_storage_failure_rolls_back_evidence_and_case(self):
        c = self.intake('https://people.rednet.cn/front/messages/detail?id=930')
        with patch.object(server.collection, 'collect', return_value=self.result(c['url'])), \
             patch.object(server.MEDIA, 'write', side_effect=OSError('synthetic write failure')):
            jobs.Worker(server).run_once()
        _, saved, _ = self.request('GET', '/api/cases/' + c['id'], who='admin')
        self.assertEqual((saved['version'], saved['evidence_count']), (1, 0))
        self.assertEqual(saved['collection_task']['state'], 'failed')
        self.assertNotIn('collection', saved)
        with server.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history WHERE case_id=?', (c['id'],)).fetchone()[0], 0)


if __name__ == '__main__': unittest.main()
