"""Bounded local collector process; no crawler dependencies in the business server."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlsplit, unquote
import weibo
import social

ROOT = Path(__file__).resolve().parent
GATE = threading.Lock()
PLATFORMS = {
    'people.rednet.cn': ('红网·百姓呼声', r'/front/messages/detail', r'id=\d+'),
    'zw.enorth.com.cn': ('北方网·政民零距离', r'/gov_open/\d+\.html', ''),
    'ly.scol.com.cn': ('四川在线·问政四川', r'/thread/details/\d+', ''),
}


def platform_for(url):
    if not isinstance(url, str) or len(url) > 3000:
        raise ValueError('原文链接格式错误')
    try:
        p = urlsplit(url)
        if p.hostname in weibo.HOSTS:
            weibo.post_target(url)
            return '微博'
        if p.hostname in social.HOSTS:
            return social.post_target(url)[1]
        spec = PLATFORMS.get(p.hostname)
        if (not spec or p.scheme != 'https' or p.username or p.password or
                p.port not in (None, 443) or p.fragment or
                not re.fullmatch(spec[1], p.path) or not re.fullmatch(spec[2], p.query)):
            raise ValueError()
    except ValueError:
        raise ValueError('请输入支持的 HTTPS 原文详情链接：红网、北方网、问政四川、微博、头条、B站、抖音图文、快手分享页或小红书笔记') from None
    return spec[0]


def source_key(url):
    try:
        return weibo.post_target(url)[0]
    except ValueError:
        try:
            return social.post_target(url)[0]
        except (ValueError, TypeError, AttributeError):
            return url


def proxy_config():
    """Only the selected proxy reaches the isolated collector process."""
    try:
        if os.environ.get('XIN_CRAWLER_PROXY_CONFIG'):
            config = json.loads(os.environ['XIN_CRAWLER_PROXY_CONFIG'])
            server = config['server']; user = config.get('username', ''); password = config.get('password', '')
        else:
            mode = os.environ.get('XIN_CRAWLER_PROXY', 'direct').lower().strip()
            if mode == 'direct':
                return None
            if mode == 'juliang':
                host = os.environ.get('juliang_proxy_host', '')
                server = (host if '://' in host else 'http://' + host) + ':' + os.environ.get('juliang_proxy_port', '')
                user = os.environ.get('juliang_proxy_user', ''); password = os.environ.get('juliang_proxy_pass', '')
            elif mode == 'openwrt':
                server = os.environ.get('op_proxy_url_http') or os.environ.get('op_proxy_url_socks5', '')
                user = password = ''
            elif mode == 'custom':
                server = os.environ.get('XIN_CRAWLER_PROXY_SERVER', '')
                user = os.environ.get('XIN_CRAWLER_PROXY_USERNAME', ''); password = os.environ.get('XIN_CRAWLER_PROXY_PASSWORD', '')
            else:
                raise ValueError()
        p = urlsplit(server)
        scheme = 'socks5' if p.scheme == 'socks5h' else p.scheme
        if (scheme not in ('http', 'https', 'socks5') or not p.hostname or not p.port or
                p.path not in ('', '/') or p.query or p.fragment or any(c.isspace() for c in p.hostname)):
            raise ValueError()
        user = user or unquote(p.username or ''); password = password or unquote(p.password or '')
        if not isinstance(user, str) or not isinstance(password, str) or bool(user) != bool(password):
            raise ValueError()
        if scheme == 'socks5' and user:
            raise ValueError('当前浏览器不支持带认证的SOCKS5，请配置HTTP代理入口')
        host = '[' + p.hostname + ']' if ':' in p.hostname else p.hostname
        result = {'server': scheme + '://' + host + ':' + str(p.port)}
        if user:result.update(username=user, password=password)
        return result
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        if str(exc) == '当前浏览器不支持带认证的SOCKS5，请配置HTTP代理入口':
            raise
        raise ValueError('采集代理配置无效，请检查代理选择、协议、端口和认证；未回退直连') from None


def collect(url):
    platform_for(url)
    proxy = proxy_config()
    default = ROOT / 'runtime' / 'crawl4ai-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python = Path(os.environ.get('XIN_CRAWLER_PYTHON') or default)
    if not python.is_file():
        raise ValueError('采集环境未安装，请按 docs/采集验证.md 配置独立 Python 环境')
    if not GATE.acquire(blocking=False):
        raise ValueError('已有采集任务运行，请稍后重试')
    try:
        # Only the selected proxy credentials may accompany the public URL.
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'HOME', 'USERPROFILE',
            'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PLAYWRIGHT_BROWSERS_PATH',
        }}
        env['PYTHONIOENCODING'] = 'utf-8'
        if proxy:env['XIN_CRAWLER_PROXY_CONFIG'] = json.dumps(proxy)
        work = ROOT / 'runtime' / 'crawler-workdir'
        work.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([str(python.resolve()), str(ROOT / 'scripts' / 'collect_page.py'), url],
                                cwd=work, env=env, capture_output=True, encoding='utf-8', timeout=75)
        if result.returncode or len(result.stdout) > 18 * 1024 * 1024:
            raise ValueError('采集进程异常，请检查独立采集环境；未写入快照')
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get('status') not in ('ok', 'restricted', 'failed'):
            raise ValueError('采集返回格式错误')
        return report
    except subprocess.TimeoutExpired:
        raise ValueError('采集超过75秒，请稍后重试；未写入快照') from None
    except (OSError, json.JSONDecodeError):
        raise ValueError('采集进程未能返回结果；未写入快照') from None
    finally:
        GATE.release()
