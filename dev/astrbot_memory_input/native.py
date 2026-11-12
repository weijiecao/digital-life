"""Convert native conversation history to lossless, source-labelled records."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, asdict

class InputError(ValueError):
    pass

# AstrBot v4.29.3 persists internal ``_checkpoint`` segments inside
# ``Conversation.history`` (``CheckpointMessageSegment``); they link an LLM turn
# to the platform history and are stripped before the provider sees them. They
# are not user facts, so they are never turned into source records, and their
# presence must not reject an otherwise valid snapshot.
INTERNAL_ROLES = frozenset({'_checkpoint'})

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

def is_internal_segment(message) -> bool:
    """Return whether a history entry is an AstrBot-internal segment."""
    return isinstance(message, dict) and message.get('role') in INTERNAL_ROLES

def _checkpoint_id(content):
    """Best-effort cursor id for an internal checkpoint segment; never a fact."""
    if isinstance(content, dict):
        value = content.get('id')
        return value if isinstance(value, str) and value else None
    value = getattr(content, 'id', None)
    return value if isinstance(value, str) and value else None

def split_internal_segments(history):
    """Split raw history into message segments and internal-segment metadata.

    Ordinals are assigned to the *kept* messages, so inserting or removing an
    internal checkpoint does not shift the identity of any user/assistant turn.
    """
    messages = []
    checkpoint_ids = []
    for message in history:
        if is_internal_segment(message):
            checkpoint_id = _checkpoint_id(message.get('content'))
            if checkpoint_id is not None:
                checkpoint_ids.append(checkpoint_id)
            continue
        messages.append(message)
    meta = {'internal_segments': len(history) - len(messages),
            'checkpoint_ids': checkpoint_ids[-16:]}
    if checkpoint_ids:
        meta['last_checkpoint'] = checkpoint_ids[-1]
    return messages, meta

@dataclass(frozen=True)
class Origin:
    platform: str
    bot: str
    user: str
    persona: str
    conversation: str
    def __post_init__(self):
        if any(not isinstance(v,str) or not v.strip() for v in asdict(self).values()):
            raise InputError('origin requires platform/bot/user/persona/conversation')
    @property
    def scope_id(self):
        return 'native-' + digest([self.platform,self.bot,self.user,self.persona])
    @property
    def stream_id(self):
        return digest(asdict(self))

@dataclass(frozen=True)
class Record:
    key: str
    scope_id: str
    stream_id: str
    ordinal: int
    native_id: str | None
    role: str
    text: str
    recorded_at: str | None
    content_hash: str
    raw_json: str
    origin_json: str
    identity_mode: str
    schema_version: int = 1
    def as_dict(self):
        return asdict(self)

def _history(payload):
    # history is the native list, a JSON-encoded list, or a conversation dict.
    if isinstance(payload, dict):
        if 'history' not in payload:
            raise InputError('conversation requires history')
        payload=payload['history']
    if isinstance(payload,str):
        try:
            payload=json.loads(payload, parse_constant=lambda s: (_ for _ in ()).throw(InputError('non-finite JSON')))
        except (ValueError,TypeError) as exc:
            raise InputError('invalid history JSON') from exc
    if not isinstance(payload,list):
        raise InputError('history must be a list')
    return payload

def parse_history_with_metadata(payload, origin: Origin):
    """Parse history into records and remember internal-segment cursor metadata."""
    history=_history(payload)
    if isinstance(payload,dict):
        for field,expected in [('cid',origin.conversation),('persona_id',origin.persona)]:
            if payload.get(field) not in (None,'',expected):
                raise InputError('origin mismatch: '+field)
    messages, meta = split_internal_segments(history)
    records=[]; seen=set(); modes=set()
    for ordinal,message in enumerate(messages):
        if not isinstance(message,dict):
            raise InputError(f'message {ordinal} must be an object')
        role=message.get('role')
        if role not in {'system','developer','user','assistant','tool','function'}:
            raise InputError(f'unsupported role at {ordinal}')
        content=message.get('content')
        if content is None:
            text=''
        elif isinstance(content,str):
            text=content
        elif isinstance(content,list):
            parts=[]
            for part in content:
                if not isinstance(part,dict) or not isinstance(part.get('type'),str):
                    raise InputError(f'invalid content part at {ordinal}')
                if part['type']=='text':
                    if not isinstance(part.get('text'),str):
                        raise InputError(f'invalid text part at {ordinal}')
                    parts.append(part['text'])
            text='\n'.join(parts)
        else:
            raise InputError(f'invalid content at {ordinal}')
        native_id=message.get('message_id',message.get('id'))
        if native_id is not None:
            if isinstance(native_id,bool) or not isinstance(native_id,(str,int)) or not str(native_id):
                raise InputError('invalid native message identity')
            native_id=str(native_id)
        mode='native_id' if native_id is not None else 'full_prefix'
        modes.add(mode)
        key=digest([origin.stream_id, mode, native_id if native_id is not None else ordinal])
        if key in seen:
            raise InputError('duplicate message identity in one input')
        seen.add(key)
        stamp=message.get('timestamp')
        if stamp is not None and (isinstance(stamp,bool) or not isinstance(stamp,(str,int,float))):
            raise InputError('invalid timestamp')
        # Unknown/non-text fields remain in raw_json. No attachment downloads,
        # summary expansion, provenance guessing, or semantic grouping occur here.
        try:
            raw=canonical(message)
        except (ValueError,TypeError) as exc:
            raise InputError('message is not finite JSON') from exc
        records.append(Record(key,origin.scope_id,origin.stream_id,ordinal,native_id,
                              role,text,None if stamp is None else str(stamp),digest(message),
                              raw,canonical(asdict(origin)),mode))
    if len(modes)>1:
        raise InputError('mixed native-ID and positional history is ambiguous')
    return records, meta

def parse_history(payload, origin: Origin) -> list[Record]:
    return parse_history_with_metadata(payload, origin)[0]
