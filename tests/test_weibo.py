"""Synthetic Weibo fixtures; run in the isolated crawler environment."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collection
import weibo
from scripts.collect_page import parse_page

MID = '3501756485200075'
URL = 'https://weibo.com/2/detail/' + MID


def fixture(uid='1234567890', mid=MID, content_mid=MID, toolbar=None):
    post = {'id': mid, 'uid': uid, 'header': {'name': '测试作者', 'createAt': '11-11 18:00'},
            'content': {'id': content_mid, 'uid': uid, 'textRaw': '仅用于解析验证的公开正文'},
            'toolbar': toolbar or {'repost': 0, 'comment': 2, 'like': 3}}
    frame = '1:' + json.dumps(['$', 'component', None, {'data': post}], ensure_ascii=False)
    return '<script>self.__next_f.push(' + json.dumps([1, frame], ensure_ascii=False) + ')</script><div>推荐内容不属于原文</div>'


class WeiboTests(unittest.TestCase):
    def test_supported_links_and_alias_deduplication(self):
        for link in (URL, 'https://www.weibo.com/1234567890/z0JH2lOMb',
                     'https://m.weibo.cn/status/z0JH2lOMb', 'https://m.weibo.cn/detail/' + MID,
                     'https://weibo.com/1234567890/' + MID + '?refer_flag=share'):
            with self.subTest(link=link):
                self.assertEqual(weibo.post_target(link)[0], URL)
                self.assertEqual(collection.platform_for(link), '微博')
                self.assertEqual(collection.source_key(link), URL)
        for link in ('https://weibo.com.evil.test/2/detail/' + MID, 'https://weibo.com/u/1234567890',
                     'http://weibo.com/2/detail/' + MID, 'https://user@weibo.com/2/detail/' + MID,
                     'https://weibo.com:444/2/detail/' + MID, 'https://t.cn/example'):
            with self.subTest(link=link), self.assertRaises(ValueError): weibo.post_target(link)

    def test_post_identity_partial_time_and_zero_interactions(self):
        r = parse_page(URL, fixture())
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['collection_state'], 'partial')
        self.assertEqual(r['interactions']['repost'], 0)
        self.assertNotIn('推荐', r['content'])
        self.assertEqual(r['published_display'], '11-11 18:00')
        self.assertEqual(r['published_at'], '')
        self.assertIsNone(r['fans'])
        self.assertIsNone(r['posts_per_day7'])
        self.assertEqual(parse_page(URL, fixture(content_mid='other'))['status'], 'failed')
        self.assertEqual(parse_page('https://weibo.com/9999999999/' + MID, fixture())['status'], 'failed')
        self.assertEqual(parse_page(URL, '<h1 id="tips_title">Access Verification</h1>')['status'], 'restricted')
        self.assertEqual(parse_page(URL, '<title>登录微博</title>')['status'], 'failed')

    def test_exact_fans_uid_and_evidence_integrity(self):
        payload = {'ok': 1, 'data': {'user': {'id': 1234567890, 'idstr': '1234567890',
                   'screen_name': '测试作者', 'followers_count': 163445, 'followers_count_str': '16.3万', 'verified': True}}}
        fields = weibo.profile_fields(payload, '1234567890')
        self.assertEqual(fields['fans'], 163445)
        self.assertNotIn('followers_count_str', fields)
        with self.assertRaises(ValueError): weibo.profile_fields(payload, '9999999999')
        for fans in ('16.3万', None, -1, True):
            payload['data']['user']['followers_count'] = fans
            with self.assertRaises(ValueError): weibo.profile_fields(payload, '1234567890')
        ev = weibo.structured_evidence(URL, '2026-11-15T00:00:00+00:00', fields)
        self.assertEqual(hashlib.sha256(ev['json'].encode()).hexdigest(), ev['sha256'])
        self.assertEqual(json.loads(ev['json'])['fans'], 163445)


if __name__ == '__main__': unittest.main()
