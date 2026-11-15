"""旧能力的受控适配：只扩展检索意图，不写人格、不绕过证据准入。"""

import re
import time
from collections import OrderedDict

FOLLOWUP = re.compile(
    r"^(?:那|这|它|后来|最后|结果|之前|那个|这个|那件事|这件事).{0,20}[呢吗？?。！!]*$"
)


class FollowupContext:
    def __init__(self, enabled=True, ttl=300):
        self.enabled = enabled
        self.ttl = ttl
        self.turns = OrderedDict()

    def remember(self, origin, message):
        key = (origin.scope_id, origin.conversation)
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            return
        if any(w in text for w in ("换个话题", "不聊这件事", "结束这个话题")):
            self.turns.pop(key, None)
            return
        mid = str(message.get("message_id", ""))
        turns = [v for v in self.turns.get(key, []) if v[0] != mid][-9:]
        turns.append((mid, text[:400], time.monotonic()))
        self.turns[key] = turns
        self.turns.move_to_end(key)
        while len(self.turns) > 1024:
            self.turns.popitem(last=False)

    def resolve(self, origin, query):
        if not self.enabled or len(query) > 24 or not FOLLOWUP.fullmatch(query.strip()):
            return query
        for _, text, stamp in reversed(
            self.turns.get((origin.scope_id, origin.conversation), [])
        ):
            if time.monotonic() - stamp > self.ttl:
                continue
            if (
                text.strip() != query.strip()
                and len(text) >= 4
                and not FOLLOWUP.fullmatch(text)
            ):
                return text + "\n后续问题：" + query
        return query

    def clear(self):
        self.turns.clear()
