import tempfile
import unittest
from pathlib import Path

from memory_engine.engine import _answerable, _provenance_allows
from memory_engine.engine import MemoryEngine
from v1_5_consolidator.contracts import SourceEnvelope
from v1_5_consolidator.store import V15Store


def source(seq, text):
    import hashlib
    return SourceEnvelope("s", seq, hashlib.sha256(text.encode()).hexdigest(), "t", text)


class TestEvidenceAdmission(unittest.TestCase):
    def test_current_value_is_supported(self):
        self.assertTrue(_answerable("我的安排是西入口", "木桥市集最终确认西入口"))

    def test_unsupported_value_is_rejected(self):
        self.assertFalse(_answerable("木桥市集最终确认北入口", "木桥市集最终确认西入口"))

    def test_unrecorded_attribute_is_rejected(self):
        self.assertFalse(_answerable("木桥市集的付款凭证", "木桥市集最终确认西入口"))

    def test_historical_and_withdrawn_branches_are_rejected(self):
        text = "木桥市集最终确认西入口"
        for query in ("木桥市集初稿", "原来的木桥市集", "撤回便签木桥市集",
                      "已废弃选项木桥市集", "备份方案木桥市集", "隔壁组专属木桥市集"):
            with self.subTest(query=query):
                self.assertFalse(_answerable(query, text))

    def test_semantic_paraphrase_can_cross_character_overlap(self):
        self.assertTrue(_answerable("木拱桥边的周末聚会", "木桥市集最终确认西入口", 0.55))

    def test_generic_topic_is_not_admitted_by_low_semantic_score(self):
        self.assertFalse(_answerable("我的牙科复查怎么安排", "木桥市集最终确认西入口", 0.42))

    def test_empty_content_never_passes(self):
        self.assertFalse(_answerable("我的安排是什么呢", "木桥市集最终确认西入口", 0.99))

    def test_subject_and_story_provenance(self):
        self.assertFalse(_provenance_allows("我周六去哪", {"subject": "friend"}))
        self.assertTrue(_provenance_allows("朋友周六去哪", {"subject": "friend"}))
        self.assertFalse(_provenance_allows("故事里的角色去哪", {"reality_status": "real"}))
        self.assertTrue(_provenance_allows("故事里的角色去哪", {"reality_status": "fictional"}))
        self.assertFalse(_provenance_allows("真实的我周六去哪", {"reality_status": "fictional"}))

    def test_revision_keeps_source_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(Path(d) / "memory.sqlite3")
            engine = MemoryEngine(store)
            event = engine.ingest("s", source(1, "旧安排"),
                                  provenance={"narrator": "user", "subject": "friend",
                                              "reality_status": "real", "story_id": None})
            self.assertTrue(event.receipt_ok)
            revised = engine.revise_source("s", 1, source(2, "新安排"))
            self.assertEqual(store.event_provenance(revised)["subject"], "friend")
            store.close()


if __name__ == "__main__":
    unittest.main()
