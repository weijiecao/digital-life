"""Incremental local posting lists, derived only from reviewed event sources."""
import hashlib

INDEX_VERSION='cjk-bigram-all-v1'

class LexicalIndex:
    def __init__(self,flow,tokenize):
        self.flow=flow
        self.db=flow.db
        self.tokenize=tokenize
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS dl_lex_events (
            scope TEXT NOT NULL, event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            PRIMARY KEY(scope,event_id));
        CREATE TABLE IF NOT EXISTS dl_lex_terms (
            scope TEXT NOT NULL, term TEXT NOT NULL, event_id TEXT NOT NULL,
            PRIMARY KEY(scope,term,event_id));
        CREATE INDEX IF NOT EXISTS dl_lex_event_terms ON dl_lex_terms(scope,event_id);
        CREATE TABLE IF NOT EXISTS dl_lex_dirty (
            scope TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY(scope,event_id));
        CREATE TABLE IF NOT EXISTS dl_lex_meta (
            key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS dl_lex_phase_insert AFTER INSERT ON e417_phase BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(NEW.scope,NEW.event_id);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_phase_delete AFTER DELETE ON e417_phase BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(OLD.scope,OLD.event_id);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_phase_update AFTER UPDATE ON e417_phase BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(OLD.scope,OLD.event_id);
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(NEW.scope,NEW.event_id);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_message_update AFTER UPDATE ON messages BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=NEW.scope AND message_id=NEW.id;
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_ingest_update AFTER UPDATE ON e417_ingest BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=NEW.scope AND message_id=NEW.message_id;
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_ingest_insert AFTER INSERT ON e417_ingest BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=NEW.scope AND message_id=NEW.message_id;
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_ingest_delete AFTER DELETE ON e417_ingest BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=OLD.scope AND message_id=OLD.message_id;
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_event_update AFTER UPDATE OF title ON events BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(NEW.scope,NEW.id);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_event_delete AFTER DELETE ON events BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty VALUES(OLD.scope,OLD.id);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_relation_insert AFTER INSERT ON relations BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=NEW.scope AND message_id IN (NEW.newer,NEW.older);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_relation_delete AFTER DELETE ON relations BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=OLD.scope AND message_id IN (OLD.newer,OLD.older);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_lex_relation_update AFTER UPDATE ON relations BEGIN
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=OLD.scope AND message_id IN (OLD.newer,OLD.older);
            INSERT OR IGNORE INTO dl_lex_dirty SELECT scope,event_id FROM e417_phase
                WHERE scope=NEW.scope AND message_id IN (NEW.newer,NEW.older);
        END;
        INSERT OR IGNORE INTO dl_lex_dirty(scope,event_id)
            SELECT DISTINCT p.scope,p.event_id FROM e417_phase p
            WHERE NOT EXISTS (SELECT 1 FROM dl_lex_events e WHERE e.scope=p.scope AND e.event_id=p.event_id);
        ''')
        version=self.db.execute("SELECT value FROM dl_lex_meta WHERE key='version'").fetchone()
        if not version or version['value']!=INDEX_VERSION:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO dl_lex_dirty SELECT DISTINCT scope,event_id FROM e417_phase')
                self.db.execute("INSERT OR REPLACE INTO dl_lex_meta VALUES('version',?)",(INDEX_VERSION,))
        self.db.commit()

    @staticmethod
    def fingerprint(snapshot):
        return hashlib.sha256((snapshot['digest']+'\0'+snapshot['title']).encode('utf-8')).hexdigest()

    def synchronize(self,scope):
        """Transactionally rebuild dirty events; a failed rebuild stays dirty."""
        dirty=list(self.db.execute('SELECT event_id FROM dl_lex_dirty WHERE scope=? ORDER BY event_id',(scope,)))
        for row in dirty:
            eid=row['event_id']
            with self.db:
                # The first write takes a SQLite lock before reading the snapshot.
                self.db.execute('DELETE FROM dl_lex_terms WHERE scope=? AND event_id=?',(scope,eid))
                self.db.execute('DELETE FROM dl_lex_events WHERE scope=? AND event_id=?',(scope,eid))
                snap=self.flow._snapshot(scope,eid)
                if snap and not snap['stale']:
                    terms=self.tokenize(snap['title'])
                    for source in snap['rows']:
                        terms.update(self.tokenize(source['content']))
                    self.db.execute('INSERT INTO dl_lex_events VALUES(?,?,?)',(scope,eid,self.fingerprint(snap)))
                    self.db.executemany('INSERT INTO dl_lex_terms VALUES(?,?,?)',((scope,term,eid) for term in terms))
                self.db.execute('DELETE FROM dl_lex_dirty WHERE scope=? AND event_id=?',(scope,eid))

    def candidates(self,scope,terms):
        self.synchronize(scope)
        terms=sorted(terms)
        if not terms:return {}
        result={}
        # Input is bounded by the plugin's 400-character message limit.
        for start in range(0,len(terms),400):
            batch=terms[start:start+400]
            placeholders=','.join('?' for _ in batch)
            query=f'''SELECT t.event_id,t.term,e.fingerprint FROM dl_lex_terms t
                JOIN dl_lex_events e ON e.scope=t.scope AND e.event_id=t.event_id
                WHERE t.scope=? AND t.term IN ({placeholders}) AND NOT EXISTS
                  (SELECT 1 FROM dl_lex_dirty d WHERE d.scope=t.scope AND d.event_id=t.event_id)'''
            for row in self.db.execute(query,(scope,*batch)):
                item=result.setdefault(row['event_id'],{'fingerprint':row['fingerprint'],'terms':set()})
                item['terms'].add(row['term'])
        return result
