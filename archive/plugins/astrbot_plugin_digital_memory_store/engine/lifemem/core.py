"""Offline evidence-first memory foundation. Standard library only."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

class SourceConflict(Exception): pass
class MissingRecord(Exception): pass

def utc(value):
    try: dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError) as e: raise ValueError('ISO time required') from e
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('timezone offset required')
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')

def required(x, name):
    if not isinstance(x, str) or not x.strip(): raise ValueError(f'{name} must not be empty')
    return x.strip()

class MemoryCore:
    def __init__(self, path=':memory:'):
        self.db = sqlite3.connect(str(path))
        self._deferred_writes=False
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA secure_delete=ON')
        # An independent prototype must not silently treat a legacy plugin DB as its own.
        app_id = self.db.execute('PRAGMA application_id').fetchone()[0]
        existing = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        if (app_id not in (0, 0x4C494645)) or (app_id == 0 and existing):
            self.db.close()
            raise ValueError('unknown database: do not open an existing plugin database')
        if str(path) != ':memory:': self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS messages (
          scope TEXT NOT NULL, id TEXT NOT NULL, origin TEXT NOT NULL,
          speaker TEXT NOT NULL CHECK(speaker IN ('user','assistant')),
          kind TEXT NOT NULL CHECK(kind IN ('user_message','assistant_delivered')),
          content TEXT NOT NULL CHECK(length(content)>0),
          recorded_at TEXT NOT NULL, occurred_at TEXT,
          PRIMARY KEY(scope,id), UNIQUE(scope,origin));
        CREATE INDEX IF NOT EXISTS ix_time ON messages(scope,recorded_at);
        CREATE TABLE IF NOT EXISTS events (
          scope TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
          PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS membership (
          scope TEXT NOT NULL, event_id TEXT NOT NULL, message_id TEXT NOT NULL,
          PRIMARY KEY(scope,event_id,message_id),
          FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE CASCADE,
          FOREIGN KEY(scope,message_id) REFERENCES messages(scope,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS relations (
          scope TEXT NOT NULL, newer TEXT NOT NULL, older TEXT NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('corrects','supersedes','retracts','related')),
          PRIMARY KEY(scope,newer,older,kind),
          FOREIGN KEY(scope,newer) REFERENCES messages(scope,id) ON DELETE CASCADE,
          FOREIGN KEY(scope,older) REFERENCES messages(scope,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS summaries (
          scope TEXT NOT NULL, id TEXT NOT NULL, event_id TEXT,
          content TEXT NOT NULL, review TEXT NOT NULL DEFAULT 'candidate',
          validity TEXT NOT NULL DEFAULT 'active',
          PRIMARY KEY(scope,id),
          FOREIGN KEY(scope,event_id) REFERENCES events(scope,id) ON DELETE SET NULL);
        CREATE TABLE IF NOT EXISTS evidence (
          scope TEXT NOT NULL, summary_id TEXT NOT NULL, message_id TEXT NOT NULL,
          PRIMARY KEY(scope,summary_id,message_id),
          FOREIGN KEY(scope,summary_id) REFERENCES summaries(scope,id) ON DELETE CASCADE,
          FOREIGN KEY(scope,message_id) REFERENCES messages(scope,id) ON DELETE CASCADE);
        CREATE TRIGGER IF NOT EXISTS source_delete BEFORE DELETE ON messages BEGIN
          UPDATE summaries SET validity='invalidated', content='[derived memory invalidated]' WHERE scope=OLD.scope AND id IN
             (SELECT summary_id FROM evidence WHERE scope=OLD.scope AND message_id=OLD.id);
        END;
        ''')
        self.has_fts = False
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(content,scope UNINDEXED,mid UNINDEXED, tokenize='trigram')")
            self.db.executescript('''
            CREATE TRIGGER IF NOT EXISTS fts_add AFTER INSERT ON messages BEGIN
             INSERT INTO fts(content,scope,mid) VALUES(NEW.content,NEW.scope,NEW.id);
            END;
            CREATE TRIGGER IF NOT EXISTS fts_del AFTER DELETE ON messages BEGIN
             DELETE FROM fts WHERE scope=OLD.scope AND mid=OLD.id;
            END;
            ''')
            self.has_fts = True
        except sqlite3.OperationalError:
            pass # SQLite build without FTS5 trigram: literal substring fallback
        self.db.execute('PRAGMA application_id=1279870533')
        self.db.execute('PRAGMA user_version=1')
        self.db.commit()

    def close(self): self.db.close()
    @contextmanager
    def write_scope(self):
        # 批量事务明确接管提交时，内部操作不能提前提交或回滚外层事务。
        if self._deferred_writes:
            yield
        else:
            with self.db:yield

    @contextmanager
    def deferred_writes(self):
        if self._deferred_writes or not self.db.in_transaction:
            raise RuntimeError('explicit outer transaction required')
        self._deferred_writes=True
        try:yield
        finally:self._deferred_writes=False
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def _get(self, table, scope, key):
        row = self.db.execute(f'SELECT * FROM {table} WHERE scope=? AND id=?',(scope,key)).fetchone()
        if row is None: raise MissingRecord(f'{table} not present in scope')
        return row

    def append(self, *, scope, origin, speaker, kind, content, recorded_at, occurred_at=None):
        scope,origin = required(scope,'scope'),required(origin,'origin')
        # 校验内容非空，但原文首尾的空格与换行也是证据的一部分。
        if not isinstance(content,str) or not content.strip():
            raise ValueError('content must not be empty')
        if (speaker,kind) not in {('user','user_message'),('assistant','assistant_delivered')}:
            raise ValueError('only user messages and actually delivered assistant replies')
        recorded_at = utc(recorded_at)
        occurred_at = utc(occurred_at) if occurred_at else None
        with self.write_scope():
            old = self.db.execute('SELECT * FROM messages WHERE scope=? AND origin=?',(scope,origin)).fetchone()
            if old:
                if (speaker,kind,content,recorded_at,occurred_at) != (old['speaker'],old['kind'],old['content'],old['recorded_at'],old['occurred_at']):
                    raise SourceConflict('same origin used for different evidence')
                return old['id']
            mid = 'M-'+uuid4().hex[:16]
            self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                            (scope,mid,origin,speaker,kind,content,recorded_at,occurred_at))
        return mid

    def create_event(self, scope, title):
        scope,title=required(scope,'scope'),required(title,'title')
        event_id='E-'+uuid4().hex[:16]
        with self.write_scope(): self.db.execute('INSERT INTO events VALUES(?,?,?)',(scope,event_id,title))
        return event_id

    def attach(self, scope, event_id, message_id):
        self._get('events',scope,event_id); self._get('messages',scope,message_id)
        with self.write_scope():
            self.db.execute('INSERT OR IGNORE INTO membership VALUES(?,?,?)',(scope,event_id,message_id))

    def relate(self, scope, newer, older, kind):
        if kind not in ('corrects','supersedes','retracts','related') or newer==older: raise ValueError('invalid relation')
        self._get('messages',scope,newer); self._get('messages',scope,older)
        with self.write_scope(): self.db.execute('INSERT OR IGNORE INTO relations VALUES(?,?,?,?)',(scope,newer,older,kind))

    def summarize(self, scope, content, evidence_ids, event_id=None, reviewed=False):
        scope,content=required(scope,'scope'),required(content,'content')
        mids=list(dict.fromkeys(evidence_ids))
        if not mids: raise ValueError('summary needs at least one source')
        if event_id: self._get('events',scope,event_id)
        for mid in mids: self._get('messages',scope,mid)
        sid='S-'+uuid4().hex[:16]
        with self.write_scope():
            self.db.execute('INSERT INTO summaries VALUES(?,?,?,?,?,?)',
                            (scope,sid,event_id,content,'reviewed' if reviewed else 'candidate','active'))
            self.db.executemany('INSERT INTO evidence VALUES(?,?,?)',[(scope,sid,mid) for mid in mids])
        return sid

    def read_summary(self, scope, summary_id):
        row=dict(self._get('summaries',scope,summary_id))
        row['evidence_ids']=[r['message_id'] for r in self.db.execute(
            'SELECT message_id FROM evidence WHERE scope=? AND summary_id=?',(scope,summary_id))]
        return row

    def remove_source(self, scope, message_id):
        self._get('messages',scope,message_id)
        with self.write_scope(): self.db.execute('DELETE FROM messages WHERE scope=? AND id=?',(scope,message_id))

    def timeline(self, scope, event_id=None):
        if event_id:
            self._get('events',scope,event_id)
            rows=self.db.execute('''SELECT m.* FROM messages m JOIN membership e
                ON m.scope=e.scope AND m.id=e.message_id WHERE m.scope=? AND e.event_id=?
                ORDER BY m.recorded_at,m.id''',(scope,event_id))
        else:
            rows=self.db.execute('SELECT * FROM messages WHERE scope=? ORDER BY recorded_at,id',(scope,))
        return [dict(row) for row in rows]

    def search(self, scope, keyword, limit=10, speaker='user'):
        """Only literal keyword matching. This is NOT a natural-language query interpreter.

        FTS5 trigram works for 3+ characters. For shorter Chinese phrases
        SQLite substring search is deliberately used instead.
        """
        scope,keyword=required(scope,'scope'),required(keyword,'keyword')
        if speaker not in ('user','assistant') or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('invalid speaker or limit')
        if self.has_fts and len(keyword)>=3 and '"' not in keyword and '\n' not in keyword:
            try:
                rows=self.db.execute('''SELECT m.*, -bm25(fts) AS rank FROM fts
                   JOIN messages m ON fts.scope=m.scope AND fts.mid=m.id
                   WHERE fts MATCH ? AND m.scope=? AND m.speaker=?
                   ORDER BY rank DESC, m.recorded_at DESC LIMIT ?''',
                   ('"'+keyword+'"',scope,speaker,limit)).fetchall()
                return [{**dict(r),'mode':'fts5_trigram'} for r in rows]
            except sqlite3.OperationalError: pass
        rows=self.db.execute('''SELECT * FROM messages
            WHERE scope=? AND speaker=? AND instr(content,?)>0
            ORDER BY recorded_at DESC,id DESC LIMIT ?''',(scope,speaker,keyword,limit)).fetchall()
        return [{**dict(r),'rank':1.0,'mode':'literal_substring'} for r in rows]

    def context(self, scope, keyword, budget_chars=600, limit=10):
        """Never silently truncate evidence that might contain negation."""
        if type(budget_chars) is not int or budget_chars<1: raise ValueError('invalid character budget')
        hits=self.search(scope,keyword,limit)
        included,skipped,lines=[],[],[]
        length=0
        for row in hits:
            line=f"[{row['id']} | {row['recorded_at']} | {row['speaker']}] {row['content']}\n"
            if length+len(line)>budget_chars:
                skipped.append(row['id']); continue
            lines.append(line); included.append(row['id']); length+=len(line)
        return {'text':''.join(lines),'source_ids':included,'skipped_ids':skipped,
                'chars':length,'status':'found' if included else ('budget_exceeded' if skipped else 'no_match')}
