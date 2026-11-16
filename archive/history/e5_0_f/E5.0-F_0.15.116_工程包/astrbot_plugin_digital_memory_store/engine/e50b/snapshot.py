"""E5.0-B: strictly bounded MANUALLY APPROVED export, never AstrBot DB access.

The import does not authenticate the exporter: it requires a human attestation in
addition to explicit operator --authorized. Review describes provenance/link,
not objective truth. Source bytes are never transformed into summaries.
"""
from __future__ import annotations
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
from ..e418.engine import UnifiedMemoryEngine
from ..e417.writer import PHASES

class SnapshotError(ValueError): pass

FORMAT='digitallife-reviewed-snapshot-v1'
MAX_EVENTS=100
MAX_SOURCES=1000
MAX_BYTES=1024*1024

def _check_fields(d, allowed, required):
    if not isinstance(d,dict) or set(d)-set(allowed) or set(required)-set(d):
        raise SnapshotError('schema keys invalid')

def _s(v,name,max_len=1000):
    if not isinstance(v,str) or not v.strip() or len(v)>max_len or '\x00' in v:
        raise SnapshotError('invalid '+name)
    return v

def _time(s):
    _s(s,'recorded_at',60)
    try:
        parsed=datetime.fromisoformat(s.replace('Z','+00:00'))
        if not parsed.tzinfo or parsed.utcoffset() is None:raise ValueError()
    except ValueError:raise SnapshotError('recorded_at must have UTC offset') from None
    return s

def validate(data):
    _check_fields(data,('format','scope','review_attestation','events'),('format','scope','review_attestation','events'))
    if data['format']!=FORMAT:raise SnapshotError('unknown snapshot version')
    scope=_s(data['scope'],'scope',128)
    _check_fields(data['review_attestation'],('reviewed_by','reviewed_at','basis'),('reviewed_by','reviewed_at','basis'))
    a=data['review_attestation']
    _s(a['reviewed_by'],'reviewer',128)
    _time(a['reviewed_at']);_s(a['basis'],'review_basis',300)
    events=data['events']
    if not isinstance(events,list) or not 1<=len(events)<=MAX_EVENTS:
        raise SnapshotError('invalid event count')
    event_ids=set();source_ids=set();total=0
    for e in events:
        _check_fields(e,('external_id','title','sources'),('external_id','title','sources'))
        eid=_s(e['external_id'],'event id',100)
        if eid in event_ids:raise SnapshotError('duplicate external event id')
        event_ids.add(eid)
        _s(e['title'],'title',100)
        if not isinstance(e['sources'],list) or not e['sources']:
            raise SnapshotError('event must have approved sources')
        for src in e['sources']:
            _check_fields(src,('external_source_id','speaker','content','phase','recorded_at','review_state','sha256'),
                          ('external_source_id','speaker','content','phase','recorded_at','review_state','sha256'))
            sid=_s(src['external_source_id'],'source id',128)
            if sid in source_ids:raise SnapshotError('duplicate source id')
            source_ids.add(sid);total+=1
            if total>MAX_SOURCES:raise SnapshotError('too many sources')
            if src['speaker']!='user' or src['review_state']!='approved_link':
                raise SnapshotError('unreviewed or non-user record rejected')
            if src['phase'] not in PHASES:raise SnapshotError('invalid phase')
            content=_s(src['content'],'source content',8000)
            _time(src['recorded_at'])
            if not isinstance(src['sha256'],str) or sha256(content.encode('utf-8')).hexdigest()!=src['sha256']:
                raise SnapshotError('source integrity failed')
    return scope

def load_snapshot(path, *, authorized=False):
    if authorized is not True:raise SnapshotError('explicit operator authorization required')
    path=Path(path)
    if not path.is_file() or path.stat().st_size>MAX_BYTES:raise SnapshotError('snapshot missing or oversized')
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError,json.JSONDecodeError) as e:raise SnapshotError('snapshot must be UTF-8 JSON') from e
    validate(data)
    return data

def import_snapshot(flow:UnifiedMemoryEngine,data):
    # Validate EVERYTHING before the first DB write. For safety, import only to
    # new ephemeral / separately designated DB; never into an AstrBot DB.
    scope=validate(data)
    mapping={};sources={}
    for e in data['events']:
        internal=flow.core.create_event(scope,e['title'])
        mapping[e['external_id']]=internal
        for src in e['sources']:
            orig='e50b-import:'+src['external_source_id']
            result=flow.ingest(scope=scope,origin=orig,speaker='user',kind='user_message',
                               content=src['content'],recorded_at=src['recorded_at'])
            flow.review(scope=scope,origin=orig,decision='approve',
                        reviewer='SNAPSHOT_ATTESTED:'+data['review_attestation']['reviewed_by'],
                        event_id=internal,phase=src['phase'],reason='external manually reviewed snapshot')
            sources[src['external_source_id']]=result['message_id']
    return scope,mapping,sources
