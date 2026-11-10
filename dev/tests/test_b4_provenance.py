import tempfile
import unittest
from pathlib import Path

from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.provenance_guard import ProvenanceGuard
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.resolver import ConstrainedResolver
from v1_5_consolidator.store import V15Store


def seed_event(store, event_id, provenance):
    now = "2026-11-08T10:00:00+00:00"
    store.db.execute("""INSERT INTO event_candidate
        (event_id,scope_id,event_type,title,state,current_revision,narrator,subject,
         reality_status,story_id,created_at,updated_at)
        VALUES(?,?,?,?,'provisional',1,?,?,?,?,?,?)""",
        (event_id, "scope-a", "narrative", event_id,
         provenance.narrator, provenance.subject, provenance.reality_status,
         provenance.story_id, now, now))
    store.db.execute("""INSERT INTO event_revision
        (event_id,rev,summary,status,modality,created_at) VALUES(?,1,?,'unknown','unknown',?)""",
        (event_id, event_id, now))
    store.db.commit()


class TestProvenanceGuard(unittest.TestCase):
    def setUp(self):
        self.guard = ProvenanceGuard()

    def test_fictional_to_user_real_is_rejected(self):
        result = self.guard.check(
            NarrativeProvenance("story_character", "story_character", "fictional", "s1"),
            NarrativeProvenance("user", "user", "real"))
        self.assertEqual(result.action, "REJECT")
        self.assertIn("narrative_to_user_event", result.reason_codes)

    def test_user_real_to_user_real_is_allowed(self):
        result = self.guard.check(NarrativeProvenance("user", "user", "real"),
                                  NarrativeProvenance("user", "user", "real"))
        self.assertEqual(result.action, "ALLOW")

    def test_same_story_is_allowed(self):
        result = self.guard.check(
            NarrativeProvenance("user", "story_character", "fictional", "story-1"),
            NarrativeProvenance("user", "story_character", "fictional", "story-1"))
        self.assertEqual(result.action, "ALLOW")

    def test_different_story_is_rejected(self):
        result = self.guard.check(
            NarrativeProvenance("user", "story_character", "fictional", "story-1"),
            NarrativeProvenance("user", "story_character", "fictional", "story-2"))
        self.assertEqual(result.action, "REJECT")

    def test_unknown_provenance_abstains(self):
        result = self.guard.check(NarrativeProvenance(), NarrativeProvenance())
        self.assertEqual(result.action, "ABSTAIN")

    def test_unknown_candidate_policy_is_explicit(self):
        source = NarrativeProvenance("user", "user", "real")
        candidate = NarrativeProvenance()
        strict = self.guard.check(source, candidate)
        permissive = ProvenanceGuard("allow_user_real").check(source, candidate)
        self.assertEqual(strict.action, "ABSTAIN")
        self.assertEqual(permissive.action, "ALLOW")
        self.assertIn("user_real_to_unknown", permissive.reason_codes)

    def test_resolver_blocks_narrative_to_real_event(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt-real", NarrativeProvenance("user", "user", "real"))
            resolver = ConstrainedResolver(store, provenance_guard=ProvenanceGuard())
            result = resolver.resolve("scope-a", SourceEnvelope("scope-a", 1, "sha", "t", "故事"),
                                      JudgmentResult(source_seq=1, proposals=[
                                          RelationProposal("evt-real", "SAME_EVENT")],
                                          provenance=NarrativeProvenance(
                                              "story_character", "story_character", "fictional", "s1")))
            self.assertEqual(result.deferred, 1)
            self.assertEqual(result.attached, 0)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()


    def test_default_resolver_cannot_bypass_guard(self):
        with tempfile.TemporaryDirectory() as d:
            store = V15Store(str(Path(d) / "v15.sqlite3"))
            seed_event(store, "evt-real", NarrativeProvenance("user", "user", "real"))
            resolver = ConstrainedResolver(store)  # no explicit guard
            result = resolver.resolve("scope-a", SourceEnvelope("scope-a", 1, "sha", "t", "故事"),
                                      JudgmentResult(source_seq=1, proposals=[
                                          RelationProposal("evt-real", "SAME_EVENT")],
                                          provenance=NarrativeProvenance(
                                              "story_character", "story_character", "fictional", "s1")))
            self.assertEqual(result.deferred, 1)
            self.assertEqual(result.attached, 0)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_evidence").fetchone()[0], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
