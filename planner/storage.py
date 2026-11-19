"""SQLite plans, session memory, append-only events. No raw secrets."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from planner.config import KST, redact

def dumps(value):
    return json.dumps(redact(value), ensure_ascii=False, default=str)

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL, run_id TEXT NOT NULL, body TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS event_session ON events(session_id,seq);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def memory(self, session):
        with self.connect() as db:
            row = db.execute('SELECT body FROM sessions WHERE id=?', (session,)).fetchone()
        return json.loads(row[0]) if row else {}

    def remember(self, session, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?)', (session, dumps(value)))

    def log(self, event):
        with self.connect() as db:
            db.execute('INSERT INTO events(session_id,run_id,body) VALUES (?,?,?)',
                       (event['session_id'], event['run_id'], dumps(event)))

    def logs(self, session):
        with self.connect() as db:
            rows = db.execute('SELECT seq,body FROM events WHERE session_id=? ORDER BY seq', (session,)).fetchall()
        return [dict(json.loads(r[1]), seq=r[0]) for r in rows]

    def log_sessions(self):
        with self.connect() as db:
            rows = db.execute('SELECT session_id,COUNT(*),MAX(seq) FROM events GROUP BY session_id ORDER BY MAX(seq) DESC').fetchall()
        return [dict(session_id=s, event_count=c, last_seq=n) for s,c,n in rows]

    def delete_logs(self, session):
        with self.connect() as db:
            return db.execute('DELETE FROM events WHERE session_id=?', (session,)).rowcount

    def plans(self):
        with self.connect() as db:
            rows = db.execute('SELECT body FROM plans ORDER BY rowid DESC').fetchall()
        return [json.loads(r[0]) for r in rows]

    def plan(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT body FROM plans WHERE id=?', (identifier,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_plan(self, body, identifier=None):
        # One write transaction prevents missed overlap warnings during concurrent saves.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old_row = db.execute('SELECT body FROM plans WHERE id=?', (identifier,)).fetchone() if identifier else None
            if identifier and not old_row:
                raise KeyError(identifier)
            old = json.loads(old_row[0]) if old_row else {}
            identifier = identifier or uuid4().hex
            value = {**body, 'id': identifier, 'created_at': old.get('created_at', datetime.now(KST).isoformat()),
                     'updated_at': datetime.now(KST).isoformat()}
            others = [json.loads(r[0]) for r in db.execute('SELECT body FROM plans').fetchall()]
            overlap = [p['id'] for p in others if p['id'] != identifier and p['research_date'] == value['research_date']
                       and p['start_time'] < value['end_time'] and value['start_time'] < p['end_time']]
            db.execute('INSERT OR REPLACE INTO plans VALUES (?,?)', (identifier, dumps(value)))
        return {'plan': value, 'overlapping_plan_ids': overlap,
                'warning': '같은 날짜에 시간이 겹치는 계획이 있습니다. 저장은 완료했습니다.' if overlap else ''}

    def delete_plan(self, identifier):
        with self.connect() as db:
            return db.execute('DELETE FROM plans WHERE id=?', (identifier,)).rowcount
