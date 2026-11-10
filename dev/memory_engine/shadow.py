"""Offline shadow comparison for M7 contract tests."""

from __future__ import annotations


class ShadowRunner:
    def __init__(self, primary, shadow):
        self.primary = primary
        self.shadow = shadow

    def compare(self, scope_id: str, query: str, budget: int = 2000) -> dict:
        left = self.primary.build_context(scope_id, query, budget)
        right = self.shadow.build_context(scope_id, query, budget)
        left_ids = [(item["event_id"], item["title"], item["evidence"])
                    for item in left["items"]]
        right_ids = [(item["event_id"], item["title"], item["evidence"])
                     for item in right["items"]]
        left_business = [(title, evidence) for _, title, evidence in left_ids]
        right_business = [(title, evidence) for _, title, evidence in right_ids]
        return {"same_items": left_business == right_business,
                "primary_items": [item[0] for item in left_ids],
                "shadow_items": [item[0] for item in right_ids],
                "primary_degraded": left["degraded"],
                "shadow_degraded": right["degraded"],
                "primary_used_chars": left["used_chars"],
                "shadow_used_chars": right["used_chars"]}
