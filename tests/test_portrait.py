"""Synthetic account-window and HTTP checks; never use production resources."""
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

import portrait
from test_collection import TestServer
import server


def report7():
    end = datetime(2026, 11, 15, 8, tzinfo=timezone.utc)
    posts = [{'id': str(n), 'uid': '1842928594', 'published_at': (end - timedelta(days=n)).isoformat()}
             for n in range(1, 8)]
    return {'uid': '1842928594', 'captured_at': end.isoformat(),
            'account_posts7': {'uid': '1842928594', 'scope': 'account_all', 'complete': True,
                'source_url': 'https://weibo.com/u/1842928594',
                'window_start': (end - timedelta(days=7)).isoformat(), 'window_end': end.isoformat(), 'posts': posts}}


class ActivityTests(unittest.TestCase):
    def test_window_dedup_and_unrounded_daily_rate(self):
        r = report7(); posts = r['account_posts7']['posts']
        posts += [dict(posts[0]), {'id': 'old', 'uid': r['uid'], 'published_at': '2026-11-08T07:59:59+00:00'},
                  {'id': 'end', 'uid': r['uid'], 'published_at': r['captured_at']}]
        result = portrait.activity7(r)
        self.assertEqual((result['state'], result['count'], result['per_day']), ('known', 7, 1))
        posts.append({'id': 'extra', 'uid': r['uid'], 'published_at': '2026-11-14T08:00:00+00:00'})
        self.assertEqual(portrait.activity7(r)['per_day'], 8 / 7)
        r['account_posts7']['window_start'] = '2026-11-08T16:00:00+08:00'
        self.assertEqual(portrait.activity7(r)['count'], 8)

    def test_zero_requires_complete_list_and_old_totals_are_not_input(self):
        r = report7(); r['account_posts7']['posts'] = []
        self.assertEqual(portrait.activity7(r)['per_day'], 0)
        for complete in (False, 1, 'true', None):
            r['account_posts7']['complete'] = complete
            self.assertIsNone(portrait.activity7(r)['count'])
        old = {'captured_at': r['captured_at'], 'posts7': 700, 'posts_per_day7': 100}
        self.assertEqual(portrait.activity7(old)['state'], 'unavailable')
        self.assertIsNone(portrait.activity7(old)['per_day'])
        r = report7(); r['account_posts7']['complete'] = False
        result = portrait.activity7(r)
        self.assertEqual((result['state'], result['sample_count'], result['count']), ('incomplete', 7, None))

    def test_bad_account_scope_source_dates_and_window_never_produce_frequency(self):
        mutations = [lambda r: r.update(captured_at='2026-11-15'),
            lambda r: r['account_posts7'].update(uid='another'),
            lambda r: r['account_posts7'].update(scope='event_related'),
            lambda r: r['account_posts7'].update(source_url='javascript:alert(1)'),
            lambda r: r['account_posts7'].update(window_start='2026-11-07T08:00:00+00:00'),
            lambda r: r['account_posts7']['posts'][0].update(uid='another'),
            lambda r: r['account_posts7']['posts'][0].update(published_at='11-14 08:00'),
            lambda r: r['account_posts7']['posts'][0].update(published_at='2026-11-16T08:00:00+00:00'),
            lambda r: r['account_posts7']['posts'].append(dict(r['account_posts7']['posts'][0], published_at='2026-11-13T00:00:00+00:00'))]
        for mutate in mutations:
            r = report7(); mutate(r)
            with self.subTest(mutation=mutate):
                result = portrait.activity7(r)
                self.assertEqual(result['state'], 'invalid')
                self.assertIsNone(result['count']); self.assertIsNone(result['per_day'])

    def test_sources_zero_fans_false_verification_and_changed_url(self):
        r = dict(report7(), source_url='https://weibo.com/2/detail/5364128086494866',
                 nickname='公开昵称', fans=0, verified=False, published_at='', published_display='11-14 08:00')
        r['structured_evidence'] = [server.collection.weibo.structured_evidence(
            'https://weibo.com/u/1842928594', r['captured_at'], {'uid': r['uid'], 'fans': 0, 'verified': False})]
        case = {'url': r['source_url'], 'collection': r}
        result = portrait.build(case); fields = {f['key']: f for f in result['fields']}
        self.assertEqual(fields['fans']['value'], 0)
        self.assertIs(fields['verified']['value'], False)
        self.assertEqual(fields['fans']['source_url'], r['structured_evidence'][0]['source_url'])
        self.assertEqual(fields['fans']['observed_at'], r['captured_at'])
        self.assertIsNone(fields['published_at']['value'])
        self.assertIn('年份待核实', fields['published_at']['reason'])
        case['url'] = 'https://m.weibo.cn/detail/5364128086494866'
        self.assertEqual(portrait.build(case)['state'], 'observed')
        case['url'] = 'https://weibo.com/2/detail/5364128086494867'
        self.assertEqual(portrait.build(case)['state'], 'stale')
        self.assertIsNone(portrait.build(case)['activity7'])
        self.assertEqual(portrait.build({'url': case['url']})['state'], 'uncollected')


class PortraitHTTPTests(TestServer):
    def test_capture_calculates_saves_and_protects_derived_fields(self):
        body = {'url': 'https://weibo.com/2/detail/5364128086494866', 'phone': '19900000000', 'user_name': '画像隔离测试'}
        st, case, _ = self.request('POST', '/api/intake', body, 'admin'); self.assertEqual(st, 201)
        endpoint = '/api/cases/' + case['id']
        r = dict(self.result(body['url']), **report7(), platform='微博', fans=0, verified=False)
        with patch.object(server.collection, 'collect', return_value=r):
            self.assertEqual(self.collect_now('POST', endpoint + '/collect', {'expected_version': 1}, 'admin')[0], 201)
        _, saved, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(saved['collection']['posts7'], 7)
        self.assertEqual(saved['collection']['posts_per_day7'], 1)
        self.assertEqual(saved['portrait']['activity7']['count'], 7)
        self.assertTrue(all(m['value'] is None for m in saved['metrics'].values()))
        self.assertEqual(saved['binding'], 'pending')
        st, _, _ = self.request('PUT', endpoint, {'expected_version': 2,
            'portrait': {'state': 'observed', 'activity7': {'per_day': 999}},
            'collection': {'fans': 999}, 'quality_note': '人工补充，不修改采集观察'}, 'admin')
        self.assertEqual(st, 200)
        _, saved, _ = self.request('GET', endpoint, who='reviewer')
        self.assertEqual(saved['portrait']['activity7']['per_day'], 1)
        self.assertEqual(saved['collection']['fans'], 0)
        self.assertEqual(saved['phone'], '199****0000')
        with server.connect() as db:
            encrypted = db.execute('SELECT body FROM cases WHERE id=?', (case['id'],)).fetchone()[0]
            self.assertNotIn('account_posts7', encrypted)
            self.assertEqual(server.dec(encrypted)['collection']['activity7']['count'], 7)
        self.request('PUT', endpoint, {'expected_version': 3, 'url': 'https://weibo.com/2/detail/5364128086494867'}, 'admin')
        _, changed, _ = self.request('GET', endpoint, who='admin')
        self.assertEqual(changed['portrait']['state'], 'stale')
        self.assertEqual(changed['evidence_count'], 1)


if __name__ == '__main__': unittest.main()
