"""可撤销的推测主题合并，原话、已确认事实和关系表不参与修改。"""
import hashlib
import json
import uuid
from datetime import datetime,timezone
from .auto_organize import read_model_group
from .auto_model import separated


def source_has_conflict(db,scope,model_key,seq):
    # 反向判断可能在事件形成后才产生；在重新整理前隔离整个推测事件。
    contradictions=db.execute('''SELECT s.older_seq,s.newer_seq FROM dl_auto_topic_members anchor
        JOIN dl_auto_topic_members a ON a.scope=anchor.scope AND a.model_key=anchor.model_key AND a.topic_id=anchor.topic_id
        JOIN dl_auto_separations s ON s.scope=a.scope AND s.older_seq=a.seq AND s.verdict_version=1
        JOIN dl_auto_topic_members b ON b.scope=a.scope AND b.model_key=a.model_key AND b.topic_id=a.topic_id AND b.seq=s.newer_seq
        WHERE anchor.scope=? AND anchor.model_key=? AND anchor.seq=?''',(scope,model_key,seq)).fetchall()
    if any(separated(db,scope,r['older_seq'],r['newer_seq']) for r in contradictions):return True
    rows=db.execute('''SELECT m.* FROM dl_auto_topic_members a
        JOIN dl_auto_move_topics t ON t.scope=a.scope AND t.topic_id=a.topic_id
        JOIN dl_auto_topic_moves m ON m.id=t.move_id AND m.model_key=a.model_key AND m.state IN ('active','conflict')
        WHERE a.scope=? AND a.model_key=? AND a.seq=?''',(scope,model_key,seq)).fetchall()
    for row in rows:
        if row['state']=='conflict':return True
        try:
            evidence=json.loads(row['evidence_json']);pair=evidence['members']
            group=read_model_group(db,scope,pair[0])
            if not group or group['members']!=pair:return True
        except (TypeError,KeyError,ValueError,IndexError):return True
    return False


def rebuild_conflicted_topics(db,model_key,limit=50):
    """对明确存在反向线索的推测事件，清除派生归属并交还后台重建。"""
    cursor=db.execute('SELECT last_rowid FROM dl_auto_rebuild_cursor WHERE model_key=?',(model_key,)).fetchone()
    last=cursor['last_rowid'] if cursor else 0
    def candidates(after):
        return db.execute('''SELECT s.rowid AS rid,s.scope,s.older_seq,s.newer_seq,a.topic_id
            FROM dl_auto_separations s
            JOIN dl_auto_topic_members a ON a.scope=s.scope AND a.seq=s.older_seq AND a.model_key=?
            JOIN dl_auto_topic_members b ON b.scope=a.scope AND b.seq=s.newer_seq
                AND b.model_key=a.model_key AND b.topic_id=a.topic_id
            WHERE s.rowid>? AND s.verdict_version=1 ORDER BY s.rowid LIMIT ?''',
            (model_key,after,limit)).fetchall()
    rows=candidates(last)
    if not rows and last:rows=candidates(0)
    with db:
        db.execute('INSERT OR REPLACE INTO dl_auto_rebuild_cursor VALUES(?,?)',
                   (model_key,rows[-1]['rid'] if rows else 0))
    rebuilt=0
    for row in rows:
        db.execute('BEGIN IMMEDIATE')
        with db:
            scope=row['scope'];topic=row['topic_id']
            # 合并回退仍在处理时，不能覆盖其来源快照及依赖。
            if db.execute('''SELECT 1 FROM dl_auto_move_topics t JOIN dl_auto_topic_moves m ON m.id=t.move_id
                WHERE t.scope=? AND t.topic_id=? AND m.model_key=? AND m.state IN ('active','conflict') LIMIT 1''',
                (scope,topic,model_key)).fetchone():continue
            members=db.execute('SELECT COUNT(*) FROM dl_auto_topic_members WHERE scope=? AND model_key=? AND topic_id=?',
                               (scope,model_key,topic)).fetchone()[0]
            if members<2:continue
            present=db.execute('''SELECT COUNT(*) FROM dl_auto_topic_members WHERE scope=? AND model_key=?
                AND topic_id=? AND seq IN (?,?)''',(scope,model_key,topic,row['older_seq'],row['newer_seq'])).fetchone()[0]
            if present!=2:continue
            if not separated(db,scope,row['older_seq'],row['newer_seq']):continue
            # 在删除归属前固定仍有效的跨时间反向判断；其他无效判断不复活。
            edges=db.execute('''SELECT s.older_seq,s.newer_seq,s.manifest_json FROM dl_auto_separations s
                JOIN dl_auto_topic_members a ON a.scope=s.scope AND a.seq=s.older_seq AND a.model_key=? AND a.topic_id=?
                JOIN dl_auto_topic_members b ON b.scope=a.scope AND b.seq=s.newer_seq AND b.model_key=a.model_key AND b.topic_id=a.topic_id
                WHERE s.scope=? AND s.verdict_version=1''',(model_key,topic,scope)).fetchall()
            for edge in edges:
                try:manifest=json.loads(edge['manifest_json'])
                except (TypeError,ValueError):continue
                if (not isinstance(manifest,list) or len(manifest)!=2 or not all(isinstance(x,dict) for x in manifest) or
                    manifest[0].get('mode') not in ('topic_update','topic_update_search') or
                    not separated(db,scope,edge['older_seq'],edge['newer_seq'])):continue
                stable=[{'seq':x['seq'],'sha256':x['sha256'],'recorded_at':x['recorded_at'],'mode':'rebuild_split'} for x in manifest]
                db.execute('UPDATE dl_auto_separations SET manifest_json=? WHERE scope=? AND older_seq=? AND newer_seq=? AND manifest_json=?',
                    (json.dumps(stable,separators=(',',':')),scope,edge['older_seq'],edge['newer_seq'],edge['manifest_json']))
            db.execute('''DELETE FROM dl_auto_topic_skips WHERE scope=? AND model_key=? AND seq IN
                (SELECT seq FROM dl_auto_topic_members WHERE scope=? AND model_key=? AND topic_id=?)''',
                (scope,model_key,scope,model_key,topic))
            db.execute('DELETE FROM dl_auto_topic_members WHERE scope=? AND model_key=? AND topic_id=?',
                       (scope,model_key,topic))
            # 等待歧义或冲突消除的其他来源也需要重新评估新的事件结构。
            db.execute('DELETE FROM dl_auto_topic_skips WHERE scope=? AND model_key=?',(scope,model_key))
            rebuilt+=1
    return rebuilt


def merge_model_topics(db,model_key,scope,seq):
    """模型给出可能关联时，有限合并两个来源有效的多条推测事件。"""
    db.execute('BEGIN IMMEDIATE')
    with db:
        group=read_model_group(db,scope,seq)
        if not group or group['basis']!='模型提出的可能关联':return False
        pair=group['members'];members=[]
        for source in pair:
            row=db.execute('SELECT * FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',
                           (scope,source,model_key)).fetchone()
            if not row:return False
            members.append(row)
        topics=[members[0]['topic_id'],members[1]['topic_id']]
        if topics[0]==topics[1]:return False
        if any(source_has_conflict(db,scope,model_key,source) for source in pair):return False
        if db.execute('''SELECT 1 FROM dl_auto_move_topics t JOIN dl_auto_topic_moves m ON m.id=t.move_id
            WHERE t.scope=? AND t.topic_id IN (?,?) AND m.state IN ('active','conflict') LIMIT 1''',
            (scope,*topics)).fetchone():return False
        all_rows=db.execute('''SELECT m.*,c.content,c.content_sha AS actual_sha,c.recorded_at AS actual_time,c.state
            FROM dl_auto_topic_members m LEFT JOIN dl_capture c ON c.scope=m.scope AND c.seq=m.seq
            WHERE m.scope=? AND m.model_key=? AND m.topic_id IN (?,?) ORDER BY m.seq''',
            (scope,model_key,*topics)).fetchall()
        if sum(r['topic_id']==topics[0] for r in all_rows)<2 or sum(r['topic_id']==topics[1] for r in all_rows)<2:return False
        for row in all_rows:
            if (row['state']!='pending' or row['content_sha']!=row['actual_sha'] or row['recorded_at']!=row['actual_time'] or
                not isinstance(row['content'],str) or hashlib.sha256(row['content'].encode()).hexdigest()!=row['content_sha']):return False
        # 只复核实际存在的跨事件反向判断，避免逐对查询全部成员。
        oppositions=db.execute('''SELECT s.older_seq,s.newer_seq FROM dl_auto_separations s
            JOIN dl_auto_topic_members a ON a.scope=s.scope AND a.seq=s.older_seq AND a.model_key=?
            JOIN dl_auto_topic_members b ON b.scope=s.scope AND b.seq=s.newer_seq AND b.model_key=a.model_key
            WHERE s.scope=? AND s.verdict_version=1 AND
            ((a.topic_id=? AND b.topic_id=?) OR (a.topic_id=? AND b.topic_id=?))''',
            (model_key,scope,topics[0],topics[1],topics[1],topics[0]))
        if any(separated(db,scope,r['older_seq'],r['newer_seq']) for r in oppositions):return False
        # 以最早来源所属主题为目标，保存每个成员的原主题以便失效后回退。
        root=all_rows[0]['topic_id'];moved=[r for r in all_rows if r['topic_id']!=root]
        manifest=[{'seq':r['seq'],'old':r['topic_id'],'new':root,'sha':r['content_sha'],'time':r['recorded_at'],'basis':r['basis']} for r in moved]
        evidence={'kind':'model_pair','members':pair}
        move_id=uuid.uuid4().hex;now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        db.execute('INSERT INTO dl_auto_topic_moves VALUES(?,?,?,?,?,?,?)',
                   (move_id,scope,model_key,json.dumps(evidence,separators=(',',':')),
                    json.dumps(manifest,separators=(',',':')),'active',now))
        db.executemany('INSERT INTO dl_auto_move_topics VALUES(?,?,?)',[(move_id,scope,t) for t in topics])
        for item in manifest:
            db.execute("UPDATE dl_auto_topic_members SET topic_id=?,basis='模型关联推测合并' WHERE scope=? AND seq=? AND model_key=?",
                       (root,scope,item['seq'],model_key))
        return True


def recover_model_merges(db,model_key,limit=50):
    """证据失效则自动回退仍可安全回退的成员；冲突时停止使用相关主题。"""
    cursor=db.execute('SELECT last_id FROM dl_auto_topology_cursor WHERE model_key=?',(model_key,)).fetchone()
    last=cursor['last_id'] if cursor else ''
    rows=db.execute("SELECT * FROM dl_auto_topic_moves WHERE model_key=? AND state IN ('active','conflict') AND id>? ORDER BY id LIMIT ?",(model_key,last,limit)).fetchall()
    if not rows and last:
        rows=db.execute("SELECT * FROM dl_auto_topic_moves WHERE model_key=? AND state IN ('active','conflict') ORDER BY id LIMIT ?",(model_key,limit)).fetchall()
    with db:db.execute('INSERT OR REPLACE INTO dl_auto_topology_cursor VALUES(?,?)',(model_key,rows[-1]['id'] if rows else ''))
    for move in rows:
        try:
            evidence=json.loads(move['evidence_json']);pair=evidence['members']
            group=read_model_group(db,move['scope'],pair[0])
            if (move['state']=='active' and group and group['members']==pair and
                not source_has_conflict(db,move['scope'],model_key,pair[0])):continue
            manifest=json.loads(move['moves_json'])
            if not isinstance(manifest,list) or not manifest:raise ValueError('manifest')
            db.execute('BEGIN IMMEDIATE')
            with db:
                conflict=False
                dependents=db.execute('SELECT * FROM dl_auto_move_dependents WHERE move_id=?',(move['id'],)).fetchall()
                for item in manifest:
                    current=db.execute('SELECT * FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',
                                       (move['scope'],item['seq'],model_key)).fetchone()
                    if current and current['topic_id'] not in (item['new'],item['old']):
                        conflict=True
                        db.execute('INSERT OR IGNORE INTO dl_auto_move_topics VALUES(?,?,?)',
                                   (move['id'],move['scope'],current['topic_id']))
                for item in dependents:
                    current=db.execute('SELECT * FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',
                                       (item['scope'],item['seq'],item['model_key'])).fetchone()
                    if current and current['topic_id']!=item['topic_id']:
                        conflict=True
                        db.execute('INSERT OR IGNORE INTO dl_auto_move_topics VALUES(?,?,?)',
                                   (move['id'],move['scope'],current['topic_id']))
                if conflict:
                    db.execute("UPDATE dl_auto_topic_moves SET state='conflict' WHERE id=?",(move['id'],));continue
                for item in manifest:
                    current=db.execute('SELECT * FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?',
                                       (move['scope'],item['seq'],model_key)).fetchone()
                    if current and current['topic_id']==item['new'] and current['content_sha']==item['sha'] and current['recorded_at']==item['time']:
                        db.execute('UPDATE dl_auto_topic_members SET topic_id=?,basis=? WHERE scope=? AND seq=? AND model_key=?',
                                   (item['old'],item['basis'],move['scope'],item['seq'],model_key))
                    elif current and current['topic_id']==item['new']:
                        # 来源快照已变，不能把新内容强行放回旧事件；移除推测归属并重新定位。
                        db.execute('DELETE FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=? AND topic_id=?',
                                   (move['scope'],item['seq'],model_key,item['new']))
                for item in dependents:
                    db.execute('''DELETE FROM dl_auto_topic_members WHERE scope=? AND seq=? AND model_key=?
                        AND topic_id=?''',
                        (item['scope'],item['seq'],item['model_key'],item['topic_id']))
                    db.execute('DELETE FROM dl_auto_topic_skips WHERE scope=? AND seq=? AND model_key=?',
                               (item['scope'],item['seq'],item['model_key']))
                # 原先因事件冲突而跳过的来源可能不属于合并清单，也需要重新定位。
                db.execute('DELETE FROM dl_auto_topic_skips WHERE scope=? AND model_key=?',(move['scope'],model_key))
                db.execute("UPDATE dl_auto_topic_moves SET state='reverted' WHERE id=?",(move['id'],))
        except (ValueError,KeyError,TypeError,IndexError):
            db.rollback()
            with db:db.execute("UPDATE dl_auto_topic_moves SET state='conflict' WHERE id=?",(move['id'],))
