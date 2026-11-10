"""Deterministic memory-type projection rules for the M3 slice."""

from __future__ import annotations

PLAN_MARKERS = ("打算", "计划", "准备", "准备去", "要搬", "要买")
PREFERENCE_MARKERS = ("喜欢", "讨厌", "偏好", "最爱", "不喜欢")
EXPERIENCE_MARKERS = ("去了", "吃了", "见了", "买了", "搬到", "开始")
STORY_MARKERS = ("故事", "小说", "剧本", "虚构")


def classify_memory(text: str) -> tuple[str, str]:
    """Return (item_type, modality); rules are intentionally conservative."""
    if any(marker in text for marker in STORY_MARKERS):
        return "story", "fictional"
    if any(marker in text for marker in PLAN_MARKERS):
        return "plan", "planned"
    if any(marker in text for marker in PREFERENCE_MARKERS):
        return "preference", "asserted"
    if any(marker in text for marker in EXPERIENCE_MARKERS):
        return "experience", "asserted"
    return "statement", "asserted"
