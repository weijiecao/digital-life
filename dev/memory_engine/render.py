"""Final memory-block renderer for P07 budget checks.

The packet's budget is only meaningful over the text that is actually handed to
the model, so this module renders that text explicitly.
"""

from __future__ import annotations


def render_item(item: dict) -> str:
    event_id = item.get("event_id", "")
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


def fit_packet(packet: dict, counter, budget_tokens: int,
               header: str = "memory") -> dict:
    """Drop trailing items until the rendered block fits the token budget."""
    result = dict(packet)
    result["items"] = list(packet.get("items", []))
    while result["items"]:
        rendered = render_context(result, header)
        used = token_count(rendered, counter)
        if used <= budget_tokens:
            result["rendered"] = rendered
            result["rendered_tokens"] = used
            result["budget_enforced"] = True
            return result
        result["items"].pop()
    rendered = render_context(result, header)
    result["rendered"] = rendered
    result["rendered_tokens"] = token_count(rendered, counter)
    result["budget_enforced"] = result["rendered_tokens"] <= budget_tokens
    return result
