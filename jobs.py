"""One durable local queue; the application already enforces one database instance."""
import json
import secrets
import threading

STATES = ('queued', 'running', 'completed', 'partial', 'restricted', 'failed')
ACTIVE = ('queued', 'running')


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS collection_jobs(
      id TEXT PRIMARY KEY,case_id TEXT NOT NULL REFERENCES cases(id),actor TEXT NOT NULL,
      batch_id TEXT NOT NULL,state TEXT NOT NULL,stage TEXT NOT NULL,attempts INTEGER NOT NULL,
      expected_version INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      detail TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS collection_jobs_active_case ON collection_jobs(case_id)
      WHERE state IN ('queued','running');
    CREATE INDEX IF NOT EXISTS collection_jobs_queue ON collection_jobs(state,created_at);
    ''')


def actor_for(db, username, service):
    row = db.execute('SELECT active,roles FROM accounts WHERE username=?', (username,)).fetchone()
    roles = json.loads(row['roles']) if row else []
    if not row or not row['active'] or not set(roles).intersection({'admin', 'analyst'}):
        raise service.Invalid('发起账号已停用或失去采集权限，任务未执行')
    return {'username': username, 'roles': roles}


def view(row, service):
    item = dict(row)
    item.update(service.dec(item.pop('detail')))
    return item


def enqueue(db, case, actor, service, batch_id=''):
    row = db.execute("SELECT * FROM collection_jobs WHERE case_id=? AND state IN ('queued','running')", (case['id'],)).fetchone()
    if row:
        return view(row, service)
    state, message = 'queued', '等待后台采集，可关闭页面'
    try:
        service.collection.platform_for(case['url'])
    except ValueError:
        state, message = 'failed', '该原文链接暂不支持自动采集，档案已保留'
    jid, stamp = 'CJ-' + secrets.token_hex(8), service.now()
    db.execute('INSERT INTO collection_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (jid, case['id'], actor['username'], batch_id, state, '等待执行' if state == 'queued' else '未执行',
                0, case['version'], stamp, stamp, service.enc({'message': message})))
    service.audit(db, actor['username'], '创建采集任务', case['id'], jid)
    return view(db.execute('SELECT * FROM collection_jobs WHERE id=?', (jid,)).fetchone(), service)


def retry(db, jid, actor, service):
    row = db.execute('SELECT * FROM collection_jobs WHERE id=?', (jid,)).fetchone()
    if not row:
        raise service.Invalid('采集任务不存在')
    if row['state'] in ACTIVE:
        raise service.Invalid('任务已在队列中，请勿重复提交')
    case = service.loadcase(db, row['case_id'])
    if db.execute("SELECT 1 FROM collection_jobs WHERE case_id=? AND state IN ('queued','running')", (case['id'],)).fetchone():
        raise service.Invalid('该档案已有采集任务正在执行')
    try:
        service.collection.platform_for(case['url'])
    except ValueError:
        raise service.Invalid('该链接仍不支持自动采集，请先修改原文链接') from None
    db.execute("UPDATE collection_jobs SET state='queued',stage='等待执行',actor=?,expected_version=?,updated_at=?,detail=? WHERE id=?",
               (actor['username'], case['version'], service.now(), service.enc({'message': '已重新排队，按当前档案版本采集'}), jid))
    service.audit(db, actor['username'], '重新采集', case['id'], jid)
    return view(db.execute('SELECT * FROM collection_jobs WHERE id=?', (jid,)).fetchone(), service)


class Worker:
    def __init__(self, service):
        self.s = service
        self.stopped = threading.Event()
        self.thread = None

    def finish(self, db, row, state, message, **extra):
        db.execute('UPDATE collection_jobs SET state=?,stage=?,updated_at=?,detail=? WHERE id=?',
                   (state, '已结束', self.s.now(), self.s.enc(dict(message=message, **extra)), row['id']))
        self.s.audit(db, row['actor'], '采集任务结束', row['case_id'], row['id'] + ' / ' + state)

    def recover(self):
        with self.s.LOCK, self.s.connect() as db:
            rows = db.execute("SELECT * FROM collection_jobs WHERE state='running'").fetchall()
            for row in rows:
                db.execute("UPDATE collection_jobs SET state='queued',stage='重启后等待恢复',updated_at=?,detail=? WHERE id=?",
                           (self.s.now(), self.s.enc({'message': '服务曾中断，已恢复排队；仍校验原档案版本'}), row['id']))
                self.s.audit(db, 'system', '恢复中断采集任务', row['case_id'], row['id'])
            db.commit()

    def run_once(self):
        s = self.s
        with s.LOCK, s.connect() as db:
            row = db.execute("SELECT * FROM collection_jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return False
            row = dict(row)
            try:
                actor_for(db, row['actor'], s)
                case = s.loadcase(db, row['case_id'])
                if case['version'] != row['expected_version']:
                    raise s.Invalid('档案已更新，本任务未覆盖修改；请重新采集')
            except s.Invalid as exc:
                self.finish(db, row, 'failed', str(exc)); db.commit(); return True
            db.execute("UPDATE collection_jobs SET state='running',stage='访问原文、提取字段与截图',attempts=attempts+1,updated_at=? WHERE id=?",
                       (s.now(), row['id']))
            s.audit(db, row['actor'], '开始采集任务', row['case_id'], row['id']); db.commit()
        try:
            report = s.collection.collect(case['url'])
            with s.LOCK, s.connect() as db:
                actor = actor_for(db, row['actor'], s)
                current = s.loadcase(db, row['case_id'])
                if current['version'] != row['expected_version']:
                    raise s.Invalid('采集期间档案已更新，结果未覆盖修改；请重新采集')
                if report['status'] != 'ok':
                    state = 'restricted' if report['status'] == 'restricted' else 'failed'
                    self.finish(db, row, state, report.get('message', '采集失败，档案和历史证据已保留'))
                else:
                    result = s.apply_capture(db, actor, current, report)
                    state = 'partial' if result['collection_state'] == 'partial' else 'completed'
                    self.finish(db, row, state, '公开字段与快照已保存；部分字段待核实' if state == 'partial' else '原文与快照已保存', result=result)
                # Evidence, dossier version and terminal task state commit together.
                db.commit()
        except Exception as exc:
            message = str(exc) if isinstance(exc, (s.Invalid, ValueError)) else '采集或存储异常，结果未提交；可重新采集'
            with s.LOCK, s.connect() as db:
                self.finish(db, row, 'failed', message[:1000]); db.commit()
        return True

    def loop(self):
        while not self.stopped.is_set():
            try:
                if self.run_once():
                    continue
            except Exception as exc:
                print('COLLECTION WORKER ERROR', type(exc).__name__, flush=True)
            self.stopped.wait(2)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.recover()
        self.thread = threading.Thread(target=self.loop, name='collection-worker', daemon=True)
        self.thread.start()
