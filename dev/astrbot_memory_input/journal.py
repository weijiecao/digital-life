"""Durable import/outbox. Delivery is at-least-once to an idempotent sink."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import replace
from .native import Origin, Record, parse_history_with_metadata, canonical

class ImportConflict(ValueError):
    pass

class ImportJournal:
    def __init__(self,path):
        self.db=sqlite3.connect(str(path),timeout=5,isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS native_stream(stream_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS native_record(
          key TEXT PRIMARY KEY, stream_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
          content_hash TEXT NOT NULL, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS import_attempt(
          id INTEGER PRIMARY KEY, stream_id TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
        ''')
    def close(self):self.db.close()
    def stage(self,payload,origin: Origin):
        records,internal=parse_history_with_metadata(payload,origin)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            stream=self.db.execute('SELECT mode FROM native_stream WHERE stream_id=?',(origin.stream_id,)).fetchone()
            mode=records[0].identity_mode if records else (stream[0] if stream else 'full_prefix')
            if stream and stream[0]!=mode:
                raise ImportConflict('identity mode changed')
            prior=self.db.execute('SELECT key,content_hash FROM native_record WHERE stream_id=? ORDER BY ordinal,key',(origin.stream_id,)).fetchall()
            if mode=='full_prefix':
                incoming=[(r.key,r.content_hash) for r in records]
                if len(incoming)<len(prior) or incoming[:len(prior)]!=prior:
                    raise ImportConflict('non-prefix snapshot: truncated, edited or reordered history')
            # Preflight the whole batch; a conflicting identity cannot partially import.
            for record in records:
                old=self.db.execute('SELECT content_hash FROM native_record WHERE key=?',(record.key,)).fetchone()
                if old and old[0]!=record.content_hash:
                    raise ImportConflict('native identity content changed: '+record.key)
            added=0
            for record in records:
                # Stable-ID deltas have batch-local offsets. Persist first-seen
                # order across batches; never overwrite an existing record's order.
                if mode=='native_id':
                    record=replace(record,ordinal=len(prior)+added)
                added+=self.db.execute('INSERT OR IGNORE INTO native_record(key,stream_id,ordinal,content_hash,payload) VALUES(?,?,?,?,?)',
                    (record.key,record.stream_id,record.ordinal,record.content_hash,canonical(record.as_dict()))).rowcount
            if records:
                self.db.execute('INSERT OR IGNORE INTO native_stream VALUES(?,?)',(origin.stream_id,mode))
            staged={'new':added,'duplicates':len(records)-added}
            if internal.get('internal_segments'):
                # 内部段只保留游标元数据，不作为用户事实入库。
                staged['internal_segments']=internal['internal_segments']
                if internal.get('checkpoint_ids'):
                    staged['checkpoint_ids']=internal['checkpoint_ids']
            self.db.execute('INSERT INTO import_attempt(stream_id,status,detail) VALUES(?,?,?)',(origin.stream_id,'STAGED',canonical(staged)))
            self.db.commit()
            return {'new':added,'duplicates':len(records)-added}
        except Exception as exc:
            self.db.rollback()
            self.db.execute('INSERT INTO import_attempt(stream_id,status,detail) VALUES(?,?,?)',(origin.stream_id,'REJECTED',type(exc).__name__+':'+str(exc)))
            raise
    def deliver(self,sink,limit=100):
        """sink.accept(record) durably commits by record.key and content_hash.

        A crash after accept but before this journal's ack will redeliver;
        the engine inbox must acknowledge the identical identity without effects.
        """
        rows=self.db.execute('SELECT key,payload FROM native_record WHERE delivered=0 ORDER BY rowid LIMIT ?',(max(0,limit),)).fetchall()
        sent=0
        for key,payload in rows:
            record=Record(**json.loads(payload))
            acknowledgement=sink.accept(record)
            if acknowledgement != {'key':key,'content_hash':record.content_hash}:
                raise ImportConflict('sink acknowledgement mismatch')
            self.db.execute('UPDATE native_record SET delivered=1 WHERE key=? AND content_hash=?',(key,record.content_hash))
            sent+=1
        return {'acknowledged':sent,'remaining':self.db.execute('SELECT COUNT(*) FROM native_record WHERE delivered=0').fetchone()[0]}
