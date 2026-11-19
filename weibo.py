"""Public Weibo links and rendered data; no login, credentials or paid APIs."""
import hashlib
import json
import re
from urllib.parse import urlsplit

HOSTS = {'weibo.com', 'www.weibo.com', 'm.weibo.cn'}


def post_target(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise ValueError('请输入微博 HTTPS 原文详情链接')
    uid = None
    match = re.fullmatch(r'/2/detail/(\d{10,19})', p.path) if p.hostname != 'm.weibo.cn' else re.fullmatch(r'/detail/(\d{10,19})', p.path)
    if match:
        mid = match[1]
    else:
        match = (re.fullmatch(r'/(\d{5,20})/([0-9a-zA-Z]{7,19})', p.path) if p.hostname != 'm.weibo.cn'
                 else re.fullmatch(r'/status/([0-9a-zA-Z]{7,19})', p.path))
        if not match:
            raise ValueError('微博链接需指向单条原文，暂不接受主页或短链')
        uid, bid = (match[1], match[2]) if p.hostname != 'm.weibo.cn' else (None, match[1])
        if bid.isdecimal() and len(bid) >= 16:
            mid = bid
        else:
            alphabet = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
            parts = []
            for end in range(len(bid), 0, -4):
                value = 0
                for char in bid[max(0, end - 4):end]:
                    value = value * 62 + alphabet.index(char)
                if value > 9999999:
                    raise ValueError('微博短ID格式无效')
                parts.append(str(value).zfill(7) if end > 4 else str(value))
            mid = ''.join(reversed(parts))
    if not 0 < int(mid) < 2 ** 63:
        raise ValueError('微博ID格式无效')
    return 'https://weibo.com/2/detail/' + mid, mid, uid


def parse_post(url, html):
    from bs4 import BeautifulSoup
    canonical, mid, expected_uid = post_target(url)
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script'):
        match = re.search(r'self\.__next_f\.push\((.*)\)', node.get_text(), re.S)
        if not match:
            continue
        try:
            chunk = json.loads(match[1])[1]
        except (ValueError, IndexError, TypeError):
            continue
        if not isinstance(chunk, str):
            continue
        for marker in re.finditer(r'"data":', chunk):
            try:
                post, _ = json.JSONDecoder().raw_decode(chunk[marker.end():].lstrip())
                if not isinstance(post, dict) or str(post.get('id')) != mid:
                    continue
                uid = post['uid']
                content, header = post['content'], post['header']
                if (not isinstance(uid, str) or not re.fullmatch(r'\d{5,20}', uid) or
                        str(content['id']) != mid or str(content['uid']) != uid or
                        (expected_uid and expected_uid != uid)):
                    raise ValueError('微博作者与原文链接不一致')
                body = content.get('textRaw') or content.get('text')
                name = header['name']
                if not isinstance(body, str) or not body.strip() or not isinstance(name, str) or not name.strip():
                    raise ValueError('缺少公开正文或作者')
                toolbar = post.get('toolbar') if isinstance(post.get('toolbar'), dict) else {}
                interactions = {key: value if type(value) is int and value >= 0 else None
                                for key, value in toolbar.items() if key in ('repost', 'comment', 'like')}
                title = re.sub(r'https?://\S+', '', body).replace('\u200b', '').strip()[:80] or name + '的微博'
                return {'status': 'ok', 'platform': '微博', 'collection_state': 'partial',
                        'canonical_url': canonical, 'post_id': mid, 'uid': uid, 'title': title,
                        'content': body[:100000], 'nickname': name[:300], 'published_at': '',
                        'published_display': str(header.get('createAt', ''))[:100],
                        'published_precision': 'display_only', 'interactions': interactions,
                        'fans': None, 'verified': None, 'posts7': None, 'posts_per_day7': None,
                        'profile_status': 'unavailable', 'structured_evidence': [],
                        'metrics_note': '本次仅采集未登录公开数据；页面时间缺少年份，近7天发布列表需要登录，保持未知。公开账号数据不代表姓名与手机号绑定已核实，不自动进入评分。'}
            except (KeyError, TypeError, ValueError):
                continue
    return {'status': 'failed', 'platform': '微博', 'message': '未找到与链接ID及作者匹配的公开微博原文；未写入快照'}


def profile_fields(payload, uid):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict) or not isinstance(payload['data'].get('user'), dict):
        raise ValueError('未取得公开主页资料')
    user = payload.get('data', {}).get('user', {})
    if payload.get('ok') != 1 or str(user.get('id')) != uid or str(user.get('idstr', uid)) != uid:
        raise ValueError('主页资料与原文作者不一致')
    fans = user.get('followers_count')
    if type(fans) is not int or fans < 0:
        raise ValueError('未公开精确粉丝数')
    return {'uid': uid, 'nickname': str(user.get('screen_name', ''))[:300], 'fans': fans,
            'verified': user.get('verified') if type(user.get('verified')) is bool else None,
            'verified_type': user.get('verified_type') if type(user.get('verified_type')) is int else None}


def structured_evidence(source_url, captured_at, fields):
    raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return {'source_url': source_url, 'captured_at': captured_at, 'json': raw,
            'sha256': hashlib.sha256(raw.encode()).hexdigest(), 'scope': '公开页面返回字段摘录',
            'kind': 'profile' if 'fans' in fields else 'post'}
