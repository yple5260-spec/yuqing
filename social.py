"""Anonymous detail-page adapters. Only data matching the requested post is used."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

LINKS = {
    'www.toutiao.com': ('今日头条', r'/w/(\d{10,20})/?', True),
    'www.bilibili.com': ('哔哩哔哩', r'/video/(BV[0-9A-Za-z]{10})/?', True),
    'www.douyin.com': ('抖音', r'/note/(\d{10,20})/?', False),
    'c.kuaishou.com': ('快手', r'/fw/photo/([0-9A-Za-z]{8,32})/?', False),
    'www.xiaohongshu.com': ('小红书', r'/explore/([0-9a-f]{24})/?', False),
}
ALIASES = {host.removeprefix('www.'): host for host in LINKS if host.startswith('www.')}
HOSTS = LINKS.keys() | ALIASES.keys()
PLATFORMS = {spec[0] for spec in LINKS.values()}
DETAILS = {'快手': '.photo-page .work-info', '小红书': '#detail-title, #detail-desc'}


def post_target(url):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or
            p.port not in (None, 443) or p.fragment):
        raise ValueError('请输入已支持平台的HTTPS原文详情链接')
    host = ALIASES.get(p.hostname, p.hostname)
    platform, pattern, trailing_slash = LINKS[host]
    if platform == '哔哩哔哩' and parse_qs(p.query).get('p', ['1']) != ['1']:
        raise ValueError('当前仅支持B站视频默认分P的详情页')
    match = re.fullmatch(pattern, p.path)
    if not match:
        raise ValueError('请输入作品详情链接，暂不支持主页或跳转短链')
    path = p.path.rstrip('/') + ('/' if trailing_slash else '')
    return 'https://' + host + path, platform, match[1]


def access_gate(platform, url):
    if platform not in DETAILS:
        return False
    p = urlsplit(url)
    if platform == '快手':
        return p.hostname in ('captcha.zt.kuaishou.com', 'captcha.kuaishou.com')
    return p.hostname in ('xiaohongshu.com', 'www.xiaohongshu.com') and (
        p.path in ('/login', '/website-login') or p.path.startswith('/website-login/'))


def detail_fields(platform, soup):
    if platform == '快手':
        work = soup.select('.photo-page .work-info')
        if len(work) != 1:
            raise ValueError('作品区域不唯一')
        author = work[0].select_one('.author .txt')
        text = work[0].select_one('.desc')
        title = ''
    else:
        work = soup.select('.note-container')
        if len(work) != 1:
            raise ValueError('笔记区域不唯一')
        author = work[0].select_one('.author-wrapper .username, .author-container .username')
        text = work[0].select_one('#detail-desc')
        heading = work[0].select_one('#detail-title')
        title = heading.get_text(' ', strip=True) if heading else ''
    name = author.get_text(' ', strip=True).lstrip('@').strip() if author else ''
    body = text.get_text(' ', strip=True) if text else title
    return name, body, title


def number(value):
    if type(value) is int and 0 <= value < 2 ** 63:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,19}', value) and int(value) < 2 ** 63:
        return int(value)
    return None


def published(value):
    value = number(value)
    if value is None:
        raise ValueError('发布时间无效')
    dt = datetime.fromtimestamp(value, timezone(timedelta(hours=8)))
    if dt.year < 2000 or dt > datetime.now(dt.tzinfo) + timedelta(minutes=5):
        raise ValueError('发布时间无效')
    return dt.isoformat(timespec='seconds')


def parse_post(url, html, public_data=None):
    from bs4 import BeautifulSoup
    canonical, platform, post_id = post_target(url)
    soup = BeautifulSoup(html, 'html.parser')
    try:
        if platform == '今日头条':
            data = json.loads(unquote(soup.select_one('#RENDER_DATA').get_text()))['data']
            cell = data['itemCell']; base = cell['articleBase']; author = cell['userInfo']
            if str(data['itemId']) != post_id or str(base['groupID']) != post_id:
                raise ValueError('原文ID不一致')
            uid, name = author['userID'], author['name']
            body, title, date = base['content'], data['title'], base['publishTime']
            fans = number(author.get('followersCount'))
            stats = cell.get('itemCounter', {})
            interactions = {k: number(stats.get(v)) for k, v in
                            (('share', 'shareCount'), ('comment', 'commentCount'), ('like', 'diggCount'))}
            media_note = '留存访问时的页面截图，未单独下载原始附件。'
        elif platform == '哔哩哔哩':
            data = public_data.get('videoData') if isinstance(public_data, dict) else None
            if data is None:
                for node in soup.select('script'):
                    match = re.search(r'\b__INITIAL_STATE__\s*=\s*', node.get_text())
                    if match:
                        data = json.JSONDecoder().raw_decode(node.get_text()[match.end():])[0]['videoData']
                        break
            if not data or data['bvid'] != post_id:
                raise ValueError('原文ID不一致')
            author = data['owner']; uid, name = str(author['mid']), author['name']
            body, title, date = data.get('desc') or data['title'], data['title'], data['pubdate']
            # shortcut: SSR upData.fans may be a placeholder; verify a public profile source before using it.
            fans = None
            stats = data.get('stat', {})
            interactions = {k: number(stats.get(v)) for k, v in
                            (('share', 'share'), ('comment', 'reply'), ('like', 'like'))}
            media_note = '仅留存视频详情页截图及公开元数据，未下载或核验视频内容。'
        elif platform == '抖音':
            data = None
            for node in soup.select('script'):
                match = re.search(r'self\.__pace_f\.push\((.*)\)', node.get_text(), re.S)
                if not match:
                    continue
                try:
                    chunk = json.loads(match[1])[1]
                    if not isinstance(chunk, str):
                        continue
                    for marker in re.finditer(r'"aweme":', chunk):
                        aweme = json.JSONDecoder().raw_decode(chunk[marker.end():].lstrip())[0]
                        item = aweme['detail']
                        if aweme.get('statusCode') == 0 and item.get('awemeId') == post_id:
                            data = item
                except (ValueError, KeyError, TypeError, IndexError):
                    continue
            if not data or data.get('awemeType') != 68:
                raise ValueError('未取得与链接匹配的图文')
            author = data['authorInfo']; uid, name = author['uid'], author['nickname']
            body, title, date = data['desc'], data['desc'][:80], data['createTime']
            fans = number(author.get('followerCount'))
            stats = data.get('stats', {})
            interactions = {k: number(stats.get(v)) for k, v in
                            (('share', 'shareCount'), ('comment', 'commentCount'), ('like', 'diggCount'))}
            media_note = '仅留存页面当前展示画面，未逐张留存图文图片，不等于全部原图取证。'
        else:
            for entry in soup.select('link[rel=canonical], meta[property="og:url"]'):
                address = entry.get('href') or entry.get('content') or ''
                try:
                    actual_url, actual_platform, _ = post_target(address)
                except ValueError:
                    continue
                if actual_platform != platform or actual_url != canonical:
                    raise ValueError('页面声明的原文ID不一致')
            name, body, title = detail_fields(platform, soup)
            uid = date = fans = None
            interactions = None
            media_note = '留存访问时的作品页面截图，未逐项保存原始图片或视频。'
        if ((platform not in DETAILS and (not isinstance(uid, str) or not re.fullmatch(r'\d{1,20}', uid))) or
                not isinstance(name, str) or not name.strip() or
                not isinstance(body, str) or not body.strip() or not isinstance(title, str)):
            raise ValueError('原文或作者信息缺失')
        if platform not in DETAILS:
            body = BeautifulSoup(body, 'html.parser').get_text(' ', strip=True)
        if not body:
            raise ValueError('正文为空')
        date = published(date) if platform not in DETAILS else ''
        return {'status': 'ok', 'platform': platform, 'collection_state': 'partial',
                'canonical_url': canonical, 'post_id': post_id, 'uid': uid, 'nickname': name.strip()[:300],
                'title': (title.strip() or body[:80])[:500], 'content': body[:100000],
                'published_at': date, 'published_precision': 'second' if date else None,
                'published_timezone': 'Asia/Shanghai' if date else None,
                'fans': fans, 'verified': None, 'profile_status': 'collected' if fans is not None else 'unavailable',
                'interactions': interactions, 'posts7': None, 'posts_per_day7': None,
                'structured_evidence': [], 'media_note': media_note,
                'metrics_note': '仅采集未登录公开详情；近7天完整列表、缺失账号指标保留未知，姓名与手机号绑定待核实，不自动评分。' + media_note}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError, OSError):
        message = '未识别到详情页的原文和作者，未保存快照' if platform in DETAILS else '未取得与链接ID匹配的原文、作者及完整发布时间；未写入快照'
        return {'status': 'failed', 'platform': platform, 'message': message}
