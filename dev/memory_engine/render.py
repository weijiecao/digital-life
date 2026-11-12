"""Final memory-block renderer with a strict zero/overflow contract."""

from __future__ import annotations


def render_item(item: dict) -> str:
    event_id = item.get("event_id", "")
    if item.get("admission", {}).get("verdict") == "SUPPORTED":
        # Evidence is already a verified extract. Repeating title and summary
        # wastes budget and can introduce unsupported derived prose.
        return "- [%s] %s" % (event_id, item.get("evidence", ""))
    title = item.get("title", "")
    summary = item.get("summary", "")
    evidence = item.get("evidence", "")
    return ("- [%s] %s\n  summary: %s\n  evidence: %s"
            % (event_id, title, summary, evidence))


def render_context(packet: dict, header: str = "memory") -> str:
    parts = ["<%s>" % header,
             "scope: %s" % packet.get("scope_id", ""),
             "query: %s" % packet.get("query", "")]
    parts.extend(render_item(item) for item in packet.get("items", []))
    parts.append("</%s>" % header)
    return "\n".join(parts)


def token_count(text: str, counter) -> int:
    return counter.count(text)


def _refs(items: list[dict]) -> list[dict]:
    return [{"event_id": item.get("event_id", "")} for item in items]


def _empty(result: dict, reason: str, counter, original_count: int) -> dict:
    result["items"] = []
    result["source_refs"] = []
    result["rendered"] = ""
    result["rendered_tokens"] = 0
    result["used_chars"] = 0
    result["used_tokens"] = 0
    result["budget_enforced"] = True
    result["degraded"] = True
    reasons = list(result.get("degraded_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    result["degraded_reasons"] = reasons
    return result


def fit_packet(packet: dict, counter, budget_tokens: int,
               header: str = "memory") -> dict:
    """Fit the final block; never emit memory text beyond the budget.

    Zero/negative budget, a header that alone exceeds the budget, or all items
    being trimmed all produce an empty memory block rather than an empty shell.
    Metadata is rebuilt from the items that actually survive.
    """
    result = dict(packet)
    original_items = list(packet.get("items", []))
    original_count = len(original_items)
    result["items"] = list(original_items)
    result["budget_tokens"] = max(0, budget_tokens)
    if budget_tokens <= 0:
        return _empty(result, "zero_budget", counter, original_count)
    if not original_items:
        return _empty(result, "no_items", counter, 0)

    frame = render_context({"scope_id": packet.get("scope_id", ""),
                            "query": packet.get("query", ""), "items": []},
                           header)
    if token_count(frame, counter) > budget_tokens:
        return _empty(result, "frame_exceeds_budget", counter, original_count)

    while result["items"]:
        rendered = render_context(result, header)
        used = token_count(rendered, counter)
        if used <= budget_tokens:
            result["source_refs"] = _refs(result["items"])
            result["rendered"] = rendered
            result["rendered_tokens"] = used
            result["used_chars"] = len(rendered)
            result["used_tokens"] = used
            result["budget_enforced"] = True
            result["degraded"] = len(result["items"]) < original_count
            reasons = list(result.get("degraded_reasons") or [])
            if result["degraded"] and "items_trimmed" not in reasons:
                reasons.append("items_trimmed")
            result["degraded_reasons"] = reasons
            return result
        result["items"].pop()

    result["degraded_reasons"] = list(result.get("degraded_reasons") or [])
    if "items_exceed_budget" not in result["degraded_reasons"]:
        result["degraded_reasons"].append("items_exceed_budget")
    return _empty(result, "items_exceed_budget", counter, original_count)
