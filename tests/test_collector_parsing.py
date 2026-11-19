"""Run in the separate crawler environment; these fixtures contain no customer data."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.collect_page import parse_page


class ParsingTests(unittest.TestCase):
    def test_rednet_post_excludes_recommendations_and_replies(self):
        r = parse_page('https://people.rednet.cn/front/messages/detail?id=1', '''
          <div class="content-box"><div id="title">测试标题</div></div>
          <div class="msg-info icon-user">测试昵称</div><div class="msg-info icon-time1">2026-01-05 17:21:37</div>
          <div class="msg-content">这是用于验证正文提取的测试内容。</div>
          <div class="reply-content">回复不应计入正文</div><div id="rec">推荐不应计入正文</div>''')
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['nickname'], '测试昵称')
        self.assertNotIn('回复', r['content'])
        self.assertIsNone(r['posts_per_day7'])

    def test_enorth_masked_name_and_date_only(self):
        r = parse_page('https://zw.enorth.com.cn/gov_open/1.html', '''
          <div class="block-q"><h4><span class="info">发表人： 张*</span>
          <span class="info">提问时间： 2026-01-09</span></h4>
          <div class="content"><h5>标题： 测试标题</h5><p>这是用于验证正文提取的测试内容。</p>
          <div id="f1div">附件排除</div></div></div>''')
        self.assertEqual(r['status'], 'ok')
        self.assertEqual(r['published_precision'], 'day')
        self.assertEqual(r['nickname'], '张*')
        self.assertNotIn('附件', r['content'])

    def test_challenge_is_never_success(self):
        url = 'https://ly.scol.com.cn/thread/details/1'
        self.assertEqual(parse_page(url, '<h1 id="tips_title">Access Verification</h1>')['status'], 'restricted')
        self.assertEqual(parse_page(url, '<html>unrecognized</html>')['status'], 'failed')
        self.assertEqual(parse_page('https://people.rednet.cn/front/messages/detail?id=1', '<title>HTTP 200 error</title>')['status'], 'failed')


if __name__ == '__main__': unittest.main()
