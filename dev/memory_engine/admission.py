"""Query-side admission: bounded local policy, not general semantic entailment.

All decisions are bound to query/scope/revision/source snapshots. A quote's
existence is validated separately from the policy's semantic judgement.
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, asdict

POLICY_VERSION = "query-evidence-v4"

def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class Evidence:
    source_seq: int
    source_hash: str
    quote: str

@dataclass(frozen=True)
class Snapshot:
    scope_id: str
    event_id: str
    revision: int
    title: str
    event_type: str
    provenance: tuple
    evidence: tuple[Evidence, ...]

    @property
    def fingerprint(self) -> str:
        import json
        return digest(json.dumps(asdict(self), ensure_ascii=False, sort_keys=True))

# Supported information classes. Values must appear in source evidence, never
# merely in the derived title/summary or in a model's generated explanation.
SLOTS = (
    ("clock", r"几点|几时|时刻", r"(?:[零一二三四五六七八九十两\d]+[点时](?:半|[零一二三四五六七八九十\d]+分)?|\d{1,2}:\d{2})"),
    ("time", r"什么时候|何时|哪天|日期|时间", r"周[一二三四五六日天]|星期[一二三四五六日天]|\d+[月日号]|上午|下午|晚上|明天|后天|今天|[一二三四五六七八九十\d]+点"),
    ("money", r"多少钱|费用|价格|金额|花了多少", r"[零一二三四五六七八九十百千万两\d.]+(?:元|块|人民币)|免费"),
    ("duration", r"多久|多长时间|时长", r"[一二三四五六七八九十百两半\d.]+(?:分钟|小时|天|周|个月|年)"),
    ("reason", r"为什么|原因|为何", r"因为|由于|原因是|所以"),
    ("floor", r"几楼|哪层", r"[一二三四五六七八九十百\d]+(?:楼|层)"),
    ("place", r"哪里|哪儿|地点|哪个门|哪边|哪岸", r"[东南西北]?(?:入口|门|岸)|[一二三四五六七八九十\d]+楼|(?:在|去|到|位于)[^，。；！？\n]{2,30}"),
    ("contact", r"联系人|找谁|谁负责", r"(?:联系人|负责[人者]?|找)[：:是为 ]*[^，。；！？\n]{2,20}"),
    ("phone", r"电话|号码", r"(?<!\d)(?:1\d{10}|0\d{2,3}[- ]?\d{7,8})(?!\d)"),
    ("id", r"编号|单号", r"(?:编号|单号)[：:是为 ]*[A-Za-z0-9-]+"),
)


def requested_slots(query: str):
    slots = [(name, value) for name, question, value in SLOTS if re.search(question, query)]
    if any(name == "duration" for name, _ in slots):
        slots = [(name, value) for name, value in slots if name != "time"]
    return slots


def subject_reason(query: str, snapshot: Snapshot) -> str | None:
    provenance = dict(snapshot.provenance)
    subject = provenance.get("subject", "unknown")
    reality = provenance.get("reality_status", "unknown")
    text = "\n".join(e.quote for e in snapshot.evidence)
    family = re.search(r"妈妈|母亲|爸爸|父亲|姐姐|哥哥|弟弟|妹妹|家人", query)
    friend = re.search(r"朋友|同事", query)
    fictional = bool(re.search(r"故事|虚构|角色", query))
    own = bool(re.search(r"我|自己|本人", query)) and not (family or friend or fictional)
    expected = "family" if family else "friend" if friend else "user" if own else None
    if expected:
        if subject != "unknown" and subject != expected:
            return "subject_mismatch"
        if subject == "unknown":
            explicit = {"family": r"妈妈|母亲|爸爸|父亲|姐姐|哥哥|弟弟|妹妹|家人",
                        "friend": r"朋友|同事", "user": r"我(?!的(?:朋友|同事|妈妈|爸爸))|本人"}
            if not re.search(explicit[expected], text):
                return "subject_unestablished"
        # Family metadata alone cannot identify which family member.
        if family:
            aliases = {"妈妈":"妈妈|母亲", "母亲":"妈妈|母亲", "爸爸":"爸爸|父亲", "父亲":"爸爸|父亲"}
            if not re.search(aliases.get(family.group(), family.group()), text):
                return "family_member_unestablished"
    if fictional and reality != "fictional":
        return "fiction_unestablished"
    if own and reality == "fictional":
        return "fiction_is_not_user_experience"
    if re.search(r"真实|现实|本人", query) and reality != "real":
        return "reality_unestablished"
    return None


def decide(query: str, snapshot: Snapshot, semantic_score: float,
           legacy_check, topic_content) -> dict:
    text = "\n".join(e.quote for e in snapshot.evidence)
    base = {"scope_id": snapshot.scope_id, "event_id": snapshot.event_id,
            "revision": snapshot.revision, "query_hash": digest(query),
            "snapshot_hash": snapshot.fingerprint, "policy_version": POLICY_VERSION,
            "requested_information": [name for name, _ in requested_slots(query)],
            "semantic_score": semantic_score, "verdict": "INSUFFICIENT",
            "reason": "", "supporting_spans": []}
    reason = subject_reason(query, snapshot)
    if reason:
        return {**base, "verdict": "CONFLICT" if "mismatch" in reason else "INSUFFICIENT", "reason": reason}
    if not legacy_check(query, text, semantic_score):
        return {**base, "reason": "legacy_policy_rejected"}
    weekdays = set(re.findall(r"(?:周|星期)([一二三四五六日天])", query))
    source_days = set(re.findall(r"(?:周|星期)([一二三四五六日天])", text))
    normalize = lambda days: {"日" if day == "天" else day for day in days}
    if weekdays and source_days and not normalize(weekdays).issubset(normalize(source_days)):
        return {**base, "verdict": "CONFLICT", "reason": "weekday_mismatch"}
    if "周末" in query and source_days and not normalize(source_days) & {"六", "日"}:
        return {**base, "verdict": "CONFLICT", "reason": "weekend_mismatch"}
    # Topic matching excludes question scaffolding and requested attributes.
    topic = topic_content(query)
    topic = re.sub(r"几点|多久|需要|集合|入口|地点|时间|门票|费用|价格|金额|原因|取消|周末|最终|母亲|妈妈|朋友|同事|本人", " ", topic)
    terms = re.findall(r"[A-Za-z0-9_]+", topic)
    for run in re.findall(r"[\u4e00-\u9fff]+", topic):
        terms += [run[i:i+2] for i in range(len(run)-1)]
    if semantic_score < 0.45 and query not in text and not any(term in text for term in terms):
        return {**base, "verdict": "RELATED_ONLY", "reason": "topic_not_supported"}
    slots = requested_slots(query)
    for name, pattern in slots:
        if not re.search(pattern, text):
            return {**base, "verdict": "RELATED_ONLY", "reason": "missing_" + name}
    # Extract exact sentence spans. Keep each required slot's supporting sentence
    # rather than using title/summary as evidence. Topic queries quote full sources.
    spans = []
    for evidence in snapshot.evidence:
        if not slots or any(re.search(pattern, evidence.quote) for _, pattern in slots):
            # Retain complete evidence so negation/subject in an adjacent
            # sentence is not lost when selecting an attribute's supporting span.
            spans.append({"source_seq": evidence.source_seq, "source_hash": evidence.source_hash,
                          "start": 0, "end": len(evidence.quote), "text": evidence.quote})
    if not spans:
        return {**base, "reason": "no_supporting_span"}
    return {**base, "verdict": "SUPPORTED", "reason": "local_policy_supported", "supporting_spans": spans}


def validate(decision: dict, query: str, snapshot: Snapshot) -> bool:
    """Reject malformed, fabricated or stale certificates independently of policy."""
    if not isinstance(decision, dict) or decision.get("verdict") != "SUPPORTED":
        return False
    for key, expected in (("query_hash", digest(query)), ("scope_id", snapshot.scope_id),
                          ("event_id", snapshot.event_id), ("revision", snapshot.revision),
                          ("snapshot_hash", snapshot.fingerprint), ("policy_version", POLICY_VERSION)):
        if decision.get(key) != expected:
            return False
    spans = decision.get("supporting_spans")
    if not isinstance(spans, list) or not spans:
        return False
    for span in spans:
        if not isinstance(span, dict):
            return False
        a, b = span.get("start"), span.get("end")
        if type(a) is not int or type(b) is not int or not (0 <= a < b):
            return False
        if not any(e.source_seq == span.get("source_seq") and e.source_hash == span.get("source_hash")
                   and b <= len(e.quote) and e.quote[a:b] == span.get("text") for e in snapshot.evidence):
            return False
    return True
