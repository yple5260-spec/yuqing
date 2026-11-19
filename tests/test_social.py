"""Synthetic, ID-bound social detail fixtures; no credentials or network."""
import json
from urllib.parse import quote
import unittest
import collection
import social
from scripts.collect_page import parse_page

TT = 'https://www.toutiao.com/w/1853479850731843/'
BV = 'https://www.bilibili.com/video/BV1JcifBWEad/'
DY = 'https://www.douyin.com/note/7591486008592990714'
KS = 'https://c.kuaishou.com/fw/photo/3xexample12345'
XHS = 'https://www.xiaohongshu.com/explore/6960d441000000001a036a91'


def tt_data():
    return {'data': {'itemId': '1853479850731843', 'title': '测试微头条',
            'itemCell': {'articleBase': {'groupID': '1853479850731843', 'content': '<p>原文测试内容</p>',
                                        'publishTime': '1767616129'},
                         'userInfo': {'userID': '61276690339', 'name': '测试作者', 'followersCount': 10},
                         'itemCounter': {'shareCount': '0', 'commentCount': '2', 'diggCount': '0'}}}}


def bv_data():
    return {'videoData': {'bvid': 'BV1JcifBWEad', 'title': '测试视频标题', 'desc': '原视频描述',
             'owner': {'mid': 3546928298068210, 'name': '测试UP主'}, 'pubdate': 1767668526,
             'stat': {'share': 0, 'reply': 2, 'like': 1}}, 'upData': {'fans': 0}}


def dy_data():
    return {'statusCode': 0, 'detail': {'awemeId': '7591486008592990714', 'awemeType': 68,
             'authorInfo': {'uid': '7583632343584343097', 'nickname': '测试图文作者', 'followerCount': 14},
             'authorUserId': 7583632343584343000, 'desc': '图文原文测试内容', 'createTime': 1767530575,
             'stats': {'shareCount': 0, 'commentCount': 3, 'diggCount': 2}}}


def tt_html(data):
    return '<script id="RENDER_DATA" type="application/json">' + quote(json.dumps(data)) + '</script>'


def dy_html(data):
    chunk = '7:["$","$L9",null,' + json.dumps({'aweme': data}) + ']\n'
    return '<script>self.__pace_f.push(' + json.dumps([1, chunk]) + ')</script>'


class SocialTests(unittest.TestCase):
    def test_canonical_shares_and_boundaries(self):
        for url, platform in ((TT, '今日头条'), (BV, '哔哩哔哩'), (DY, '抖音'), (KS, '快手'), (XHS, '小红书')):
            alias = url.replace('www.', '') + '?share=tracking'
            self.assertEqual(collection.platform_for(alias), platform)
            self.assertEqual(collection.source_key(alias), url)
        for url in ('https://www.toutiao.com.evil.test/w/1853479850731843/',
                    TT.replace('https:', 'http:'), 'https://user@www.douyin.com/note/7591486008592990714',
                    'https://www.douyin.com:444/note/7591486008592990714', DY + '#other',
                    'https://www.douyin.com/user/1234', 'https://v.douyin.com/1234',
                    'https://www.douyin.com/video/7591486008592990714', BV + '?p=2',
                    'https://www.bilibili.com/video/av1234/', 'https://www.toutiao.com/article/1234/',
                    'https://c.kuaishou.com/abc', 'https://xhslink.com/a/abc',
                    XHS.replace('/explore/', '/user/profile/')):
            with self.subTest(url=url), self.assertRaises(ValueError):collection.platform_for(url)

    def test_toutiao_linked_author_exact_fans_and_seconds(self):
        r = parse_page(TT, tt_html(tt_data()) + '<p>相关推荐不能计入原文</p>')
        self.assertEqual(r['status'], 'ok');self.assertEqual(r['uid'], '61276690339')
        self.assertEqual(r['fans'], 10);self.assertEqual(r['content'], '原文测试内容')
        self.assertEqual(r['published_at'], '2026-01-05T20:28:49+08:00')
        self.assertEqual(r['interactions']['like'], 0);self.assertIsNone(r['posts7'])
        self.assertEqual(r['interactions']['share'], 0);self.assertNotIn('repost', r['interactions'])

    def test_toutiao_mismatch_missing_and_approximate_metrics(self):
        for field in ('itemId', 'groupID'):
            data = tt_data()
            obj = data['data'] if field == 'itemId' else data['data']['itemCell']['articleBase']
            obj[field] = '9999999999999'
            self.assertEqual(parse_page(TT, tt_html(data))['status'], 'failed')
        data = tt_data();data['data']['itemCell']['userInfo']['followersCount'] = '1.2万'
        self.assertIsNone(parse_page(TT, tt_html(data))['fans'])

    def test_bilibili_state_and_html_identify_video_without_placeholder_fans(self):
        data = bv_data()
        for html, state in (('<script>window.__INITIAL_STATE__ = ' + json.dumps(data) + ';</script>', None),
                            ('<h1>测试视频标题</h1>', data)):
            r = parse_page(BV, html, state)
            self.assertEqual(r['status'], 'ok');self.assertIsNone(r['fans'])
            self.assertEqual(r['content'], '原视频描述');self.assertEqual(r['uid'], '3546928298068210')
            self.assertEqual(r['published_at'], '2026-01-06T11:02:06+08:00')
            self.assertIn('未下载', r['media_note'])
        data['videoData']['bvid'] = 'BV9JcifBWEad'
        self.assertEqual(parse_page(BV, '<h1>推荐视频</h1>', data)['status'], 'failed')

    def test_douyin_matching_pace_data_and_string_account_id(self):
        r = parse_page(DY, dy_html(dy_data()))
        self.assertEqual(r['status'], 'ok');self.assertEqual(r['uid'], '7583632343584343097')
        self.assertEqual(r['fans'], 14);self.assertEqual(r['collection_state'], 'partial')
        self.assertEqual(r['published_at'], '2026-01-04T20:42:55+08:00')
        self.assertIsNone(r['posts_per_day7']);self.assertIn('未逐张', r['media_note'])
        data = dy_data();data['detail']['awemeId'] = '9999999999999999999'
        self.assertEqual(parse_page(DY, dy_html(data))['status'], 'failed')
        data = dy_data();data['detail']['awemeType'] = 0
        self.assertEqual(parse_page(DY, dy_html(data))['status'], 'failed')

    def test_malformed_restricted_and_future_dates_never_succeed(self):
        for url in (TT, BV, DY):
            for html in ('<title>登录</title><p>推荐作者和内容</p>', '<script>window.__INITIAL_STATE__=alert(1)</script>'):
                self.assertEqual(parse_page(url, html)['status'], 'failed')
            self.assertEqual(parse_page(url, '<h1>安全验证</h1>')['status'], 'restricted')
        for value in (True, -1, '1.2万', 99999999999999999):
            data = dy_data();data['detail']['createTime'] = value
            self.assertEqual(parse_page(DY, dy_html(data))['status'], 'failed')

    def test_public_detail_accepts_only_one_readable_work(self):
        fixtures = (
            (KS, '<div class="photo-page"><div class="work-info"><div class="author"><span class="txt">测试作者</span></div>'
                 '<p class="desc">测试公开正文 &lt;原样&gt;</p></div></div>'),
            (XHS, '<div class="note-container"><div class="author-wrapper"><span class="username">测试作者</span></div>'
                  '<h1 id="detail-title">原创笔记</h1><p id="detail-desc">测试公开正文 &lt;原样&gt;</p></div>'),
        )
        for url, html in fixtures:
            report = parse_page(url, html + '<p>推荐内容</p>')
            self.assertEqual(report['status'], 'ok')
            self.assertEqual(report['collection_state'], 'partial')
            self.assertEqual(report['content'], '测试公开正文 <原样>')
            self.assertEqual(report['nickname'], '测试作者')
            self.assertIsNone(report['uid']);self.assertIsNone(report['fans']);self.assertEqual(report['published_at'], '')
            self.assertEqual(parse_page(url, html + html)['status'], 'failed')
            self.assertEqual(parse_page(url, '<title>安全限制</title>' + html)['status'], 'restricted')
            different_id = url[:-1] + ('2' if url[-1] != '2' else '3')
            self.assertEqual(parse_page(url, f'<link rel="canonical" href="{different_id}">' + html)['status'], 'failed')

    def test_access_gate_links_are_not_work_targets(self):
        for platform, url in (('快手', 'https://captcha.zt.kuaishou.com/mobile/h5/redirect/index.html'),
                              ('小红书', 'https://www.xiaohongshu.com/website-login/error')):
            self.assertTrue(social.access_gate(platform, url))
            self.assertFalse(social.access_gate(platform, url.replace('.com/', '.com.evil.test/')))
            with self.assertRaises(ValueError):collection.platform_for(url)


if __name__ == '__main__':unittest.main()
