import unittest

from v1_5_consolidator.candidate_retriever import (
    EmbeddingCandidateIndex,
    EventAnchor,
    LexicalCandidateIndex,
)


class FakeEncoder:
    def encode_documents(self, texts):
        return [[1.0 if "熊猫" in text else 0.0, 1.0 if "火箭" in text else 0.0]
                for text in texts]

    def encode_queries(self, texts):
        return self.encode_documents(texts)


class TestIndexUpdate(unittest.TestCase):
    def test_lexical_same_id_update_replaces_postings(self):
        index = LexicalCandidateIndex()
        index.add(EventAnchor("e1", "s", canonical_title="熊猫"))
        self.assertEqual([c.event_id for c in index.search("s", "熊猫")], ["e1"])
        index.add(EventAnchor("e1", "s", canonical_title="火箭", version=2))
        self.assertEqual(index.search("s", "熊猫"), [])
        self.assertEqual([c.event_id for c in index.search("s", "火箭")], ["e1"])

    def test_embedding_same_id_update_replaces_vector(self):
        index = EmbeddingCandidateIndex(FakeEncoder())
        index.add(EventAnchor("e1", "s", canonical_title="熊猫"))
        self.assertGreater(index.search("s", "熊猫")[0].score, 0)
        index.add(EventAnchor("e1", "s", canonical_title="火箭", version=2))
        self.assertEqual(index.search("s", "熊猫")[0].score, 0)
        self.assertGreater(index.search("s", "火箭")[0].score, 0)


if __name__ == "__main__":
    unittest.main()
