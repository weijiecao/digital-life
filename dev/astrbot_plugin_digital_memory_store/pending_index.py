"""Incremental, scoped word fragments for unreviewed captured messages."""
import hashlib

class PendingIndex:
    def __init__(self,db,tokenize):
        self.db=db;self.tokenize=tokenize
        db.executescript('''
        CREATE TABLE IF NOT EXISTS dl_pending_index (
            scope TEXT NOT NULL,seq INTEGER NOT NULL,content_sha TEXT NOT NULL,
            PRIMARY KEY(scope,seq));
        CREATE TABLE IF NOT EXISTS dl_pending_terms (
            scope TEXT NOT NULL,term TEXT NOT NULL,seq INTEGER NOT NULL,
            PRIMARY KEY(scope,term,seq));
        CREATE INDEX IF NOT EXISTS dl_pending_by_seq ON dl_pending_terms(scope,seq);
        CREATE TABLE IF NOT EXISTS dl_pending_dirty (
            scope TEXT NOT NULL,seq INTEGER NOT NULL,PRIMARY KEY(scope,seq));
        CREATE TRIGGER IF NOT EXISTS dl_pending_capture_insert AFTER INSERT ON dl_capture BEGIN
            INSERT OR IGNORE INTO dl_pending_dirty VALUES(NEW.scope,NEW.seq);
        END;
        CREATE TRIGGER IF NOT EXISTS dl_pending_capture_update AFTER UPDATE ON dl_capture BEGIN
            INSERT OR IGNORE INTO dl_pending_dirty VALUES(OLD.scope,OLD.seq);
            INSERT OR IGNORE INTO dl_pending_dirty VALUES(NEW.scope,NEW.seq);
        END;
        DROP TRIGGER IF EXISTS dl_pending_capture_delete;
        CREATE TRIGGER dl_pending_capture_delete AFTER DELETE ON dl_capture BEGIN
            DELETE FROM dl_pending_dirty WHERE scope=OLD.scope AND seq=OLD.seq;
        END;
        INSERT OR IGNORE INTO dl_pending_dirty
            SELECT c.scope,c.seq FROM dl_capture c WHERE c.state='pending'
              AND NOT EXISTS (SELECT 1 FROM dl_pending_index p WHERE p.scope=c.scope AND p.seq=c.seq);
        ''')
        db.commit()

    def synchronize(self,scope):
        rows=list(self.db.execute('SELECT seq FROM dl_pending_dirty WHERE scope=? ORDER BY seq',(scope,)))
        for dirty in rows:
            seq=dirty['seq']
            with self.db:
                self.db.execute('DELETE FROM dl_pending_terms WHERE scope=? AND seq=?',(scope,seq))
                self.db.execute('DELETE FROM dl_pending_index WHERE scope=? AND seq=?',(scope,seq))
                row=self.db.execute('SELECT content,content_sha,state FROM dl_capture WHERE scope=? AND seq=?',(scope,seq)).fetchone()
                if row and row['state']=='pending' and hashlib.sha256(row['content'].encode()).hexdigest()==row['content_sha']:
                    self.db.execute('INSERT INTO dl_pending_index VALUES(?,?,?)',(scope,seq,row['content_sha']))
                    self.db.executemany('INSERT INTO dl_pending_terms VALUES(?,?,?)',
                         ((scope,term,seq) for term in self.tokenize(row['content'])))
                self.db.execute('DELETE FROM dl_pending_dirty WHERE scope=? AND seq=?',(scope,seq))

    def candidates(self,scope,terms):
        self.synchronize(scope)
        terms=sorted(terms)
        if len(terms)<2:return []
        # A query is at most 400 characters; split for conservative SQLite variable limits.
        counts={}
        for start in range(0,len(terms),400):
            batch=terms[start:start+400];marks=','.join('?' for _ in batch)
            sql=f'''SELECT t.seq,t.term FROM dl_pending_terms t
                JOIN dl_pending_index p ON p.scope=t.scope AND p.seq=t.seq
                WHERE t.scope=? AND t.term IN ({marks}) AND NOT EXISTS
                    (SELECT 1 FROM dl_pending_dirty d WHERE d.scope=t.scope AND d.seq=t.seq)'''
            for row in self.db.execute(sql,(scope,*batch)):
                counts[row['seq']]=counts.get(row['seq'],0)+1
        return [seq for seq,count in counts.items() if count>=2]
