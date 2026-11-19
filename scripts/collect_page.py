"""Fetch only approved public complaint pages. Never treats a challenge page as evidence."""
import asyncio
import base64
import contextlib
from datetime import datetime, timezone
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collection import platform_for, proxy_config, source_key
import weibo
import social
from urllib.parse import urlsplit


def access_restricted(platform):
    return {'status': 'restricted', 'platform': platform,
            'message': '平台要求登录、验证或限制了访问，本次未保存快照'}


def parse_page(url, html, public_data=None):
    from bs4 import BeautifulSoup
    platform = platform_for(url)
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    visible = soup.get_text(' ', strip=True)
    challenge = soup.select_one('#tips_title, .captcha-content-container, #nc_1_wrapper')
    if platform in social.DETAILS:
        page_title = soup.title.get_text(strip=True) if soup.title else ''
        if page_title in ('安全限制', '登录小红书', '登录快手') or re.search(
                r'当前笔记暂时无法浏览|当前网络存在风险|antispam need captcha|登录后查看完整版', visible[:1500], re.I):
            return access_restricted(platform)
    if challenge or re.search(r'Access Verification|请滑动.{0,12}验证|访问验证|安全验证', visible[:1500], re.I):
        return {'status': 'restricted', 'platform': platform, 'message': '平台访问受限，本次采集已自动结束并记录；未保存验证页，不需要人工处理验证码'}
    if platform == '微博':
        return weibo.parse_post(url, html)
    if platform in social.PLATFORMS:
        return social.parse_post(url, html, public_data)

    def text(selector):
        node = soup.select_one(selector)
        return node.get_text(' ', strip=True) if node else ''

    if 'rednet.cn' in url:
        title, content = text('.content-box #title'), text('.msg-content')
        nickname, published = text('.msg-info.icon-user'), text('.msg-info.icon-time1')
    elif 'enorth.com.cn' in url:
        title = re.sub(r'^标题[：:]\s*', '', text('.block-q h5'))
        box = soup.select_one('.block-q .content')
        if box:
            for node in box.select('h5, #f1div'):
                node.decompose()
        content = box.get_text(' ', strip=True) if box else ''
        info = [node.get_text(' ', strip=True) for node in soup.select('.block-q h4 .info')]
        nickname = next((re.sub(r'^发表人[：:]\s*', '', t) for t in info if t.startswith('发表人')), '')
        published = next((re.sub(r'^提问时间[：:]\s*', '', t) for t in info if t.startswith('提问时间')), '')
    else:
        # shortcut: Sichuan has no verified unattended data channel; add its parser when one is available.
        return {'status': 'failed', 'platform': platform, 'message': '问政四川尚无已验证的自动采集通道，本次未保存快照'}
    date = re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?', published)
    if not title or len(content) < 10 or not nickname or not date or len(title) > 500:
        return {'status': 'failed', 'platform': platform, 'message': '未识别到完整原文、作者或发布时间；未写入证据'}
    try:
        datetime.fromisoformat(published)
    except ValueError:
        return {'status': 'failed', 'platform': platform, 'message': '原文发布时间无效；未写入证据'}
    return {'status': 'ok', 'platform': platform, 'title': title, 'content': content[:100000],
            'nickname': nickname[:300], 'published_at': published, 'published_timezone': 'Asia/Shanghai',
            'published_precision': 'second' if len(published) > 10 else 'day',
            'fans': None, 'posts7': None, 'posts_per_day7': None,
            'metrics_note': '当前公开详情页没有可核验粉丝数和完整作者近7天发布列表，保留未知；不按昵称合并账号，不以本库条数代替发帖总数。'}


async def capture(url):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    platform = platform_for(url)
    is_weibo = platform == '微博'
    is_social = platform in social.PLATFORMS
    target_url = source_key(url) if is_weibo or is_social else url
    # Preserve the supplied public note link parameters only for the actual page visit.
    query = urlsplit(url).query if platform == '小红书' else ''
    visit_url = target_url + '?' + query if query else target_url
    navigation_urls = {target_url}
    addresses = {}
    screenshot = {}
    public_data = {}
    profile = {}
    pending = set()
    author_uid = None
    stopped_at_gate = False

    async def public_resource(resource):
        from urllib.parse import urlsplit
        try:
            p = urlsplit(resource)
            if p.scheme not in ('https', 'http') or p.username or p.password or p.port not in (None, 80, 443):
                return False
            if p.hostname not in addresses:
                infos = await asyncio.to_thread(socket.getaddrinfo, p.hostname, None)
                addresses[p.hostname] = bool(infos) and all(ipaddress.ip_address(x[4][0]).is_global for x in infos)
            return addresses[p.hostname]
        except (OSError, ValueError):
            return False

    async def configure(page, context, **kwargs):
        async def route_request(route):
            nonlocal stopped_at_gate
            request = route.request
            if request.is_navigation_request():
                try:
                    from urllib.parse import urlsplit, parse_qs
                    parsed = urlsplit(request.url)
                    if request.frame == page.main_frame and social.access_gate(platform, request.url):
                        stopped_at_gate = True
                        return await route.abort()
                    # Weibo initializes anonymous visitor cookies through this normal redirect.
                    visitor = (is_weibo and author_uid and parsed.scheme == 'https' and
                               parsed.hostname == 'login.sina.com.cn' and parsed.path == '/visitor/visitor' and
                               parse_qs(parsed.query).get('url', [''])[0] in navigation_urls)
                    original = is_social and source_key(request.url) == target_url
                    if request.url not in navigation_urls and not visitor and not original:
                        raise ValueError()
                except ValueError:
                    return await route.abort()
            if await public_resource(request.url):
                await route.continue_()
            else:
                await route.abort()
        await context.route('**/*', route_request)
        await context.route_web_socket('**/*', lambda route: route.close())
        if is_weibo:
            from urllib.parse import urlsplit
            async def inspect(response):
                parsed = urlsplit(response.url)
                if (not author_uid or parsed.hostname != 'weibo.com' or parsed.path != '/ajax/profile/info' or
                        response.status != 200):
                    return
                try:
                    raw = await response.body()
                    if len(raw) > 1024 * 1024:
                        return
                    fields = weibo.profile_fields(json.loads(raw), author_uid)
                    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                    profile.update(fields, profile_captured_at=stamp, profile_status='collected',
                                   evidence=weibo.structured_evidence('https://weibo.com/u/' + author_uid, stamp, fields))
                except (ValueError, TypeError, AttributeError):
                    pass
            def observe(response):
                task = asyncio.create_task(inspect(response))
                pending.add(task)
                task.add_done_callback(pending.discard)
            page.on('response', observe)
        return page

    async def capture_original(page, context, html, **kwargs):
        if stopped_at_gate or social.access_gate(platform, page.url):
            return page
        if platform == '哔哩哔哩' and source_key(page.url) == target_url:
            await page.wait_for_function('()=>Boolean(window.__INITIAL_STATE__?.videoData)', timeout=8000)
            public_data.update(await page.evaluate('()=>({videoData:window.__INITIAL_STATE__.videoData})'))
        report = parse_page(url, html, public_data)
        at_target = page.url == target_url or (is_social and source_key(page.url) == target_url)
        if at_target and report['status'] == 'ok':
            if platform in social.DETAILS:
                if not await page.locator(social.DETAILS[platform]).first.is_visible():
                    return page
            elif is_social:
                await page.wait_for_function('(text)=>document.body.innerText.includes(text)',
                                             arg=report['title'][:20], timeout=12000)
                try:
                    await page.wait_for_function('Array.from(document.images).every(img => img.complete)', timeout=5000)
                except Exception:
                    pass
            else:
                await page.wait_for_function('Array.from(document.images).every(img => img.complete)', timeout=5000)
            card = page.locator('[class*="__cardSingle"]') if is_weibo else None
            if card and await card.count() != 1:
                raise ValueError('无法唯一识别微博原文卡片')
            size = (await card.evaluate('(el)=>({w:el.offsetWidth,h:el.offsetHeight})') if card else
                    await page.evaluate('({w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight})'))
            if size['w'] * size['h'] > 40000000:
                raise ValueError('原文整页超出4000万像素限制')
            raw = await card.screenshot(type='png') if card else await page.screenshot(full_page=True, type='png')
            screenshot['data'] = base64.b64encode(raw).decode()
            screenshot['captured_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return page

    out = ROOT / 'runtime' / 'crawler-cache'
    out.mkdir(parents=True, exist_ok=True)
    async with AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False, ignore_https_errors=False, proxy_config=proxy_config(),
                               viewport_width=1280, viewport_height=900), base_directory=str(out)) as crawler:
        crawler.crawler_strategy.set_hook('on_page_context_created', configure)
        crawler.crawler_strategy.set_hook('before_return_html', capture_original)
        result = await crawler.arun(url=visit_url, config=CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS, screenshot=False, page_timeout=30000,
            wait_for='js:() => Boolean(document.querySelector("#RENDER_DATA"))' if platform == '今日头条' else None,
            wait_for_timeout=12000,
            wait_until='domcontentloaded', delay_before_return_html=3,
            word_count_threshold=0, verbose=False))
        if platform in social.DETAILS and (stopped_at_gate or social.access_gate(platform, result.url) or
                                          result.status_code in (401, 403, 429)):
            return access_restricted(platform)
        report = parse_page(url, result.html or '', public_data)
        if is_weibo and report['status'] == 'ok' and result.success and screenshot:
            author_uid = report['uid']
            profile_url = 'https://weibo.com/u/' + author_uid
            navigation_urls.update({profile_url, 'https://weibo.com/' + author_uid})
            report['structured_evidence'].append(weibo.structured_evidence(target_url, screenshot['captured_at'],
                {key: report[key] for key in ('post_id', 'uid', 'nickname', 'content', 'published_display', 'interactions')}))
            try:
                await asyncio.wait_for(crawler.arun(url=profile_url, config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS, page_timeout=15000, wait_until='domcontentloaded',
                    delay_before_return_html=3, word_count_threshold=0, verbose=False)), timeout=20)
                if pending:
                    await asyncio.wait_for(asyncio.gather(*list(pending), return_exceptions=True), timeout=3)
            except Exception:
                pass
            if profile:
                report.update({key: profile[key] for key in ('fans', 'verified', 'verified_type', 'profile_status', 'profile_captured_at')})
                report['structured_evidence'].append(profile['evidence'])
    if report['status'] != 'ok':
        return report
    at_target = result.url == target_url or (is_social and source_key(result.url) == target_url)
    if not result.success or result.status_code != 200 or not at_target or not screenshot:
        return {'status': 'failed', 'platform': platform_for(url), 'message': '页面访问或截图失败；未写入快照'}
    if is_social:
        stamp = screenshot['captured_at']
        report['structured_evidence'].append(weibo.structured_evidence(target_url, stamp,
            {key: report[key] for key in ('post_id', 'uid', 'nickname', 'content', 'published_at', 'interactions')}))
        if report['fans'] is not None:
            report['profile_captured_at'] = stamp
            report['structured_evidence'].append(weibo.structured_evidence(target_url, stamp,
                {key: report[key] for key in ('uid', 'nickname', 'fans')}))
    # shortcut: populate a timeline only after a public channel can verify account and pagination completeness.
    report['account_posts7'] = {'state': 'unavailable', 'source_url':
        ('https://weibo.com/u/' + author_uid) if author_uid else url,
        'reason': '未登录无法取得完整近7天发布列表，不能计算账号发布频次。' if is_weibo else
                  '本次公开详情未提供该账号完整近7天发布列表，不能计算账号发布频次。' if is_social else
                  '该问政原文未提供唯一作者账号及完整近7天发布列表，不能按昵称归集。'}
    return dict(report, source_url=url, engine='crawl4ai-0.9.5', **screenshot)


if __name__ == '__main__':
    try:
        # Keep crawler diagnostics and page bodies out of the parent's JSON protocol and logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            report = asyncio.run(asyncio.wait_for(capture(sys.argv[1]), timeout=60))
    except Exception:
        report = {'status': 'failed', 'message': '采集超时或网络异常；未写入快照'}
    print(json.dumps(report, ensure_ascii=False))
