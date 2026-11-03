"""Persistent accounts and one human-service channel per customer (SQLite)."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + hashed


class PortalStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'portal.sqlite3'
        with self.db() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user','support','dev')), password TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS logins (
                    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS invitations (role TEXT PRIMARY KEY, token TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS owned_sessions (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id));
                CREATE INDEX IF NOT EXISTS sessions_by_user ON owned_sessions(user_id);
                CREATE TABLE IF NOT EXISTS channels (
                    user_id TEXT PRIMARY KEY REFERENCES users(id), state TEXT NOT NULL,
                    staff_id TEXT, session_id TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS human_messages (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    kind TEXT NOT NULL, author_id TEXT, author_name TEXT NOT NULL,
                    content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS messages_by_user ON human_messages(user_id, created_at);
                CREATE TABLE IF NOT EXISTS archives (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, detail TEXT NOT NULL);
            ''')
            self._migrate_services(con)
            if not con.execute('SELECT 1 FROM invitations LIMIT 1').fetchone():
                invitations = {role: secrets.token_urlsafe(24) for role in ('support', 'dev')}
                for role, token in invitations.items():
                    con.execute('INSERT INTO invitations VALUES (?,?)', (role, digest(token)))
                # Local operator bootstrap; never returned by HTTP or placed in frontend/public.
                (self.root / 'invitations.json').write_text(json.dumps(invitations, indent=2), encoding='utf-8')

    @staticmethod
    def _new_service(con, user, session_id, started_at):
        service_id = uuid4().hex
        stamp = datetime.fromisoformat(started_at).astimezone(timezone(timedelta(hours=8))).strftime('%Y%m%dT%H%M%S%f')[:-3]
        title = f"{user['id']}-{user['name']}-{stamp}"
        con.execute('INSERT INTO human_services VALUES (?,?,?,?,?,?,?,?)',
                    (service_id, user['id'], user['name'], title, session_id, started_at, None, None))
        return service_id

    def _migrate_services(self, con):
        con.execute('''CREATE TABLE IF NOT EXISTS human_services (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), user_name TEXT NOT NULL,
            title TEXT NOT NULL, session_id TEXT NOT NULL, started_at TEXT NOT NULL,
            ended_at TEXT, staff_id TEXT)''')
        con.execute('CREATE INDEX IF NOT EXISTS services_by_user ON human_services(user_id, started_at)')
        for table in ('channels', 'human_messages'):
            if 'service_id' not in {row['name'] for row in con.execute(f'PRAGMA table_info({table})')}:
                con.execute(f'ALTER TABLE {table} ADD COLUMN service_id TEXT')
        # Reconstruct legacy rounds in message order, including replies formerly routed to old sessions.
        for user in con.execute('SELECT * FROM users WHERE id IN (SELECT user_id FROM channels)').fetchall():
            current = None
            for msg in con.execute('SELECT * FROM human_messages WHERE user_id=? ORDER BY created_at,rowid', (user['id'],)).fetchall():
                if msg['service_id']:
                    current = msg['service_id']
                    continue
                if current is None or (msg['kind'] == 'system' and msg['content'].startswith('已申请人工客服')):
                    current = self._new_service(con, user, msg['session_id'], msg['created_at'])
                con.execute('UPDATE human_messages SET service_id=? WHERE id=?', (current, msg['id']))
                if msg['kind'] == 'staff' or (msg['kind'] == 'system' and '已接入' in msg['content']):
                    con.execute('UPDATE human_services SET staff_id=? WHERE id=?', (msg['author_id'], current))
                if msg['kind'] == 'system' and msg['content'].startswith('本次人工服务已结束'):
                    con.execute('UPDATE human_services SET ended_at=? WHERE id=?', (msg['created_at'], current))
            row = con.execute('SELECT * FROM channels WHERE user_id=?', (user['id'],)).fetchone()
            if not row['service_id']:
                current = current or self._new_service(con, user, row['session_id'], row['updated_at'])
                con.execute('UPDATE channels SET service_id=? WHERE user_id=?', (current, user['id']))

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def public(row):
        return {key: row[key] for key in ('id', 'username', 'name', 'role')}

    def register(self, username, name, role, password, invitation):
        hashed = password_hash(password)
        with self.db() as con:
            if role != 'user':
                expected = con.execute('SELECT token FROM invitations WHERE role=?', (role,)).fetchone()
                if not expected or not hmac.compare_digest(expected['token'], digest(invitation)):
                    raise HTTPException(403, '员工邀请码无效，请向本机项目负责人获取')
            user = dict(id=uuid4().hex, username=username.lower(), name=name, role=role, password=hashed)
            try:
                con.execute('INSERT INTO users VALUES (:id,:username,:name,:role,:password)', user)
            except sqlite3.IntegrityError:
                raise HTTPException(409, '此账号已被注册，请登录或更换账号') from None
            return self.public(user)

    def login(self, username, password, role):
        with self.db() as con:
            row = con.execute('SELECT * FROM users WHERE username=?', (username.lower(),)).fetchone()
            # Hash even for unknown accounts to reduce account-existence timing differences.
            salt = row['password'].split(':')[0] if row else '00' * 16
            candidate = password_hash(password, salt)
            if not row or not hmac.compare_digest(candidate, row['password']) or row['role'] != role:
                raise HTTPException(401, '账号、密码或登录身份不匹配')
            return self.public(row)

    def new_login(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.db() as con:
            con.execute('DELETE FROM logins WHERE expires<?', (time.time(),))
            con.execute('INSERT INTO logins VALUES (?,?,?)', (digest(token), user_id, time.time() + 43200))
        return token

    def current_user(self, token):
        if not token:
            return None
        with self.db() as con:
            row = con.execute('SELECT u.* FROM users u JOIN logins l ON u.id=l.user_id WHERE l.token=? AND l.expires>?', (digest(token), time.time())).fetchone()
            return self.public(row) if row else None

    def logout(self, token):
        with self.db() as con:
            con.execute('DELETE FROM logins WHERE token=?', (digest(token or ''),))

    def bind(self, session_id, user_id):
        with self.db() as con:
            con.execute('INSERT INTO owned_sessions VALUES (?,?)', (str(session_id), user_id))

    def owner(self, session_id):
        with self.db() as con:
            row = con.execute('SELECT user_id FROM owned_sessions WHERE session_id=?', (str(session_id),)).fetchone()
            return row['user_id'] if row else None

    def owned_ids(self, user_id):
        with self.db() as con:
            return {row['session_id'] for row in con.execute('SELECT session_id FROM owned_sessions WHERE user_id=?', (user_id,))}

    def check_session(self, user, session_id):
        owner = self.owner(session_id)
        if user['role'] == 'support' or (owner != user['id'] and not (owner is None and user['role'] == 'dev')):
            raise HTTPException(404, '会话不存在或无权访问')

    def channel(self, user_id):
        with self.db() as con:
            row = con.execute('''SELECT c.*,u.name AS staff_name,s.title AS service_title,
                s.started_at AS service_started_at FROM channels c
                LEFT JOIN users u ON u.id=c.staff_id
                LEFT JOIN human_services s ON s.id=c.service_id WHERE c.user_id=?''', (user_id,)).fetchone()
            return dict(row) if row else {'user_id': user_id, 'state': 'bot', 'revision': 0, 'staff_id': None, 'staff_name': None}

    def check_bot(self, session_id):
        owner = self.owner(session_id)
        if owner and self.channel(owner)['state'] != 'bot':
            raise HTTPException(409, '当前已转人工，请通过人工服务通道发送消息')

    def archive(self, user_id, detail):
        with self.db() as con:
            existing = con.execute('SELECT detail FROM archives WHERE session_id=?', (detail['id'],)).fetchone()
            old = json.loads(existing['detail'])['messages'] if existing else []
            keys = {(m['created_at'], m['role'], m['content']) for m in old}
            detail['messages'] = old + [m for m in detail['messages'] if (m['created_at'], m['role'], m['content']) not in keys]
            con.execute('INSERT OR REPLACE INTO archives VALUES (?,?,?)', (detail['id'], user_id, json.dumps(detail, ensure_ascii=False)))

    @staticmethod
    def add_message(con, user_id, session_id, kind, author, content):
        channel = con.execute('SELECT service_id FROM channels WHERE user_id=?', (user_id,)).fetchone()
        msg = dict(id=uuid4().hex, user_id=user_id, session_id=session_id, kind=kind,
                   author_id=author['id'], author_name=author['name'], content=content, created_at=now(),
                   service_id=channel['service_id'])
        con.execute('''INSERT INTO human_messages
            (id,user_id,session_id,kind,author_id,author_name,content,created_at,service_id) VALUES
            (:id,:user_id,:session_id,:kind,:author_id,:author_name,:content,:created_at,:service_id)''', msg)
        return msg

    def request_human(self, user, session_id):
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM channels WHERE user_id=?', (user['id'],)).fetchone()
            if row and row['state'] != 'bot':
                return
            rev = row['revision'] + 1 if row else 1
            started_at = now()
            service_id = self._new_service(con, user, session_id, started_at)
            con.execute('INSERT OR REPLACE INTO channels (user_id,state,staff_id,session_id,revision,updated_at,service_id) VALUES (?,?,?,?,?,?,?)',
                        (user['id'], 'waiting', None, session_id, rev, started_at, service_id))
            self.add_message(con, user['id'], session_id, 'system', user, '已申请人工客服，接下来的消息将由人工客服处理。')

    def staff_action(self, staff, user_id, action, revision, message=None):
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT * FROM channels WHERE user_id=?', (user_id,)).fetchone()
            if not row or row['revision'] != revision:
                raise HTTPException(409, '服务状态已更新，请刷新后再操作')
            if action == 'claim':
                if row['state'] != 'waiting' or row['staff_id']:
                    raise HTTPException(409, '此服务已被接入或已结束')
                con.execute("UPDATE channels SET state='active',staff_id=?,updated_at=? WHERE user_id=?", (staff['id'], now(), user_id))
                con.execute('UPDATE human_services SET staff_id=? WHERE id=?', (staff['id'], row['service_id']))
                self.add_message(con, user_id, row['session_id'], 'system', staff, f"{staff['name']} 已接入，为您提供人工服务。")
            else:
                if row['state'] != 'active' or row['staff_id'] != staff['id']:
                    raise HTTPException(403, '仅当前接待客服可以回复或结束服务')
                if action == 'reply':
                    # The current round owns the reply even before the customer's first message.
                    self.add_message(con, user_id, row['session_id'], 'staff', staff, message)
                    con.execute('UPDATE channels SET updated_at=? WHERE user_id=?', (now(), user_id))
                elif action == 'close':
                    con.execute("UPDATE channels SET state='bot',updated_at=? WHERE user_id=?", (now(), user_id))
                    con.execute('UPDATE human_services SET ended_at=? WHERE id=?', (now(), row['service_id']))
                    self.add_message(con, user_id, row['session_id'], 'system', staff, '本次人工服务已结束，您可以继续向自动客服提问。')

    def customer_message(self, user, session_id, message):
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT state FROM channels WHERE user_id=?', (user['id'],)).fetchone()
            if not row or row['state'] == 'bot':
                raise HTTPException(409, '人工服务已结束，请刷新后向自动客服提问')
            self.add_message(con, user['id'], session_id, 'user', user, message)
            con.execute('UPDATE channels SET session_id=?,updated_at=? WHERE user_id=?', (session_id, now(), user['id']))

    def messages(self, user_id):
        with self.db() as con:
            return [dict(row) for row in con.execute('''SELECT m.*,s.title AS service_title,
                s.session_id AS service_session_id FROM human_messages m
                LEFT JOIN human_services s ON s.id=m.service_id
                WHERE m.user_id=? ORDER BY m.created_at,m.rowid''', (user_id,))]

    def queue(self, staff_id):
        with self.db() as con:
            return [dict(row) for row in con.execute('''SELECT c.*,u.name,u.username,s.title AS service_title,
                s.started_at AS service_started_at,
                (SELECT content FROM human_messages m WHERE m.user_id=c.user_id ORDER BY created_at DESC,rowid DESC LIMIT 1) AS preview
                FROM channels c JOIN users u ON u.id=c.user_id
                LEFT JOIN human_services s ON s.id=c.service_id
                WHERE c.state='waiting' OR c.staff_id=?
                ORDER BY CASE c.state WHEN 'waiting' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,c.updated_at DESC''', (staff_id,))]

    def services(self, user_id):
        with self.db() as con:
            return [dict(row) for row in con.execute(
                'SELECT * FROM human_services WHERE user_id=? ORDER BY started_at,id', (user_id,))]

    def check_staff_view(self, staff_id, user_id):
        channel = self.channel(user_id)
        if channel['state'] != 'waiting' and channel['staff_id'] != staff_id:
            raise HTTPException(404, '服务不存在或不属于当前客服')
        return channel

    def archived(self, user_id):
        with self.db() as con:
            return [json.loads(row['detail']) for row in con.execute('SELECT detail FROM archives WHERE user_id=?', (user_id,))]
