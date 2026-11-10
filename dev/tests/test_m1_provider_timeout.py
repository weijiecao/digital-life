import hashlib
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from memory_engine.rejudge import PendingRejudger
from v1_5_consolidator.contracts import NarrativeProvenance, SourceEnvelope
from v1_5_consolidator.gateway import OpenAIProvider
from v1_5_consolidator.relation_judge import JudgmentResult, RelationProposal
from v1_5_consolidator.store import V15Store


class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(2)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


class TestProviderTimeout(unittest.TestCase):
    def test_slow_provider_times_out(self):
        server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = OpenAIProvider(
                "http://127.0.0.1:%d" % server.server_port,
                "mock", "key", timeout=0.2)
            with self.assertRaises(Exception):
                provider.generate(type("P", (), {"prompt": "hello"})())
        finally:
            server.shutdown()
            server.server_close()

    def test_timeout_keeps_pending_then_retry_resolves(self):
        server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as d:
                store = V15Store(str(Path(d) / "v15.sqlite3"))
                event_id = store.create_event_from_source(
                    "s", SourceEnvelope("s", 99, "sha", "t", "target"),
                    provenance={"narrator": "user", "subject": "user",
                                "reality_status": "real", "story_id": None})
                source = SourceEnvelope("s", 1, "sha", "t", "hello")
                store.save_pending(
                    "s", 1, [{"candidate_event_id": event_id, "label": "SAME_EVENT"}],
                    {event_id: [event_id]}, "split", source=source,
                    provenance=NarrativeProvenance("user", "user", "real"))
                store.enqueue_ready("s", 1, "recheck")
                provider = OpenAIProvider(
                    "http://127.0.0.1:%d" % server.server_port,
                    "mock", "key", timeout=0.2)

                def slow(scope, src):
                    provider.generate(type("P", (), {"prompt": "hello"})())
                    return JudgmentResult(source_seq=src.source_seq, proposals=[
                        RelationProposal(event_id, "SAME_EVENT")])

                with self.assertRaises(Exception):
                    PendingRejudger(store, slow).run_once("s")
                self.assertEqual(store.events_for_source("s", 1), [])
                self.assertEqual(store.ready_generation("s", 1), 1)

                def fast(scope, src):
                    return JudgmentResult(source_seq=src.source_seq, proposals=[
                        RelationProposal(event_id, "SAME_EVENT")])
                result = PendingRejudger(store, fast).run_once("s")
                self.assertEqual(result["resolved"], 1)
                self.assertEqual(store.events_for_source("s", 1), [event_id])
                store.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
