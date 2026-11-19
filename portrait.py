"""Public account observations; independent of identity binding and score rules."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import collection


def stamp(value):
    if not isinstance(value, str):
        raise ValueError('缺少带时区的时间')
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError('时间缺少时区')
    return value.astimezone(timezone.utc)


def source_url(value):
    try:
        p = urlsplit(value)
        return value if (isinstance(value, str) and len(value) <= 3000 and p.scheme in ('http', 'https')
                         and p.hostname and not p.username and not p.password and p.port in (None, 80, 443)) else ''
    except (ValueError, TypeError, AttributeError):
        return ''


def activity7(report):
    raw = report.get('account_posts7')
    raw = raw if isinstance(raw, dict) else {}
    result = {'state': 'unavailable', 'count': None, 'per_day': None, 'sample_count': 0,
              'window_start': '', 'window_end': '', 'observed_at': report.get('captured_at', ''),
              'source_url': source_url(raw.get('source_url', '')), 'scope': 'account_all',
              'reason': raw.get('reason') or '未取得该账号完整近7天发布列表；单条原文和本库数量不能代替账号总量。'}
    try:
        end = stamp(report.get('captured_at'))
        start = end - timedelta(days=7)
        result.update(window_start=start.isoformat(), window_end=end.isoformat())
    except (ValueError, TypeError, OverflowError):
        result.update(state='invalid', reason='采集时间无效，无法确定近7天窗口。')
        return result
    if raw.get('state') == 'unavailable' or not raw:
        return result
    try:
        uid = report.get('uid')
        if (not isinstance(uid, str) or not uid or raw.get('uid') != uid or raw.get('scope') != 'account_all'
                or not result['source_url']):
            raise ValueError('账号或统计范围、来源不明确，不能计算账号发布频次。')
        if stamp(raw.get('window_start')) != start or stamp(raw.get('window_end')) != end:
            raise ValueError('发布列表的统计窗口与本次采集前7天不一致。')
        posts = raw.get('posts')
        if not isinstance(posts, list) or len(posts) > 10000:
            raise ValueError('发布列表格式无效或超出本次处理上限。')
        seen = {}
        for post in posts:
            if (not isinstance(post, dict) or post.get('uid') != uid or
                    not isinstance(post.get('id'), str) or not post['id'] or len(post['id']) > 200):
                raise ValueError('发布记录缺少唯一ID或作者不一致，不能计算。')
            published = stamp(post.get('published_at'))
            if published > end or (post['id'] in seen and seen[post['id']] != published):
                raise ValueError('发布记录存在未来时间或同ID时间冲突，不能计算。')
            seen[post['id']] = published
        count = sum(start <= published < end for published in seen.values())
        result['sample_count'] = count
        if raw.get('complete') is not True:
            result.update(state='incomplete', reason='列表未采全，仅有部分去重样本；不推算总量或日均频次。')
        else:
            result.update(state='known', count=count, per_day=count / 7,
                          reason='该账号全部公开发布内容，按内容ID去重；窗口含开始、不含结束，日均值为数量÷7。')
    except (ValueError, TypeError, OverflowError) as exc:
        result.update(state='invalid', reason=str(exc) or '发布记录无效，无法计算。')
    return result


def build(case):
    report = case.get('collection')
    result = {'state': 'uncollected', 'fields': [], 'activity7': None,
              'reason': '尚无公开账号采集结果，可从原文采集后查看。'}
    if not isinstance(report, dict):
        return result
    original = source_url(report.get('source_url', ''))
    if not original or collection.source_key(original) != collection.source_key(case['url']):
        result.update(state='stale', reason='当前原文链接与已存采集来源不一致，请重新采集；历史快照仍保留。')
        return result
    result.update(state='observed', reason='公开账号资料不等同于已核实姓名、手机号；本区结果尚未自动进入评分。',
                  activity7=activity7(report))
    evidence = report.get('structured_evidence')
    evidence = evidence if isinstance(evidence, list) else []
    sources = {kind: next((ev for ev in evidence if isinstance(ev, dict)
                          and ev.get('kind') == kind and source_url(ev.get('source_url', ''))), {})
               for kind in ('post', 'profile')}

    def field(key, label, value, kind='post', reason=''):
        ev = sources[kind]
        url = source_url(ev.get('source_url', '')) or (original if kind == 'post' else '')
        when = ev.get('captured_at') or (report.get('captured_at', '') if kind == 'post' else report.get('profile_captured_at', ''))
        result['fields'].append({'key': key, 'label': label, 'value': value, 'source_url': url,
                                 'observed_at': when, 'reason': reason if value is None else
                                 ('' if url and when else '该值的来源或观测时间待补核。')})

    field('nickname', '作者昵称', report.get('nickname') or None, reason='原文未提供可识别昵称。')
    field('uid', '平台账号 ID', report.get('uid') or None, reason='原文未提供唯一账号ID，不按同昵称合并账号。')
    fans = report.get('fans')
    field('fans', '公开精确粉丝数', fans if type(fans) is int and fans >= 0 else None, 'profile',
          '未取得公开精确粉丝数，不以简称数量或0代替。')
    verified = report.get('verified')
    field('verified', '平台认证状态', verified if type(verified) is bool else None, 'profile',
          '未取得平台认证信息；认证不等同于实名或手机号核验。')
    field('published_at', '原文发布时间', report.get('published_at') or None,
          reason=('页面仅显示“' + str(report['published_display']) + '”，完整年份待核实。')
          if report.get('published_display') else '未取得可核验的完整发布时间。')
    if report.get('published_at'):
        result['fields'][-1]['reason'] = '日期精度：' + {'day': '日', 'second': '秒'}.get(report.get('published_precision'), '以原文为准') + '；原文时区：' + report.get('published_timezone', '待核实')
    interactions = report.get('interactions')
    if isinstance(interactions, dict):
        sharing = ('share', '累计分享') if 'share' in interactions else ('repost', '累计转发')
        for key, label in (sharing, ('comment', '累计评论'), ('like', '累计点赞')):
            value = interactions.get(key)
            field(key, label, value if type(value) is int and value >= 0 else None,
                  reason='未取得精确累计数；累计量不等于每小时新增量。')
    return result
