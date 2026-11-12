"""数字记忆控制台：查询、维护、只读边界与 HTTP 接口。"""

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

from dlm_console.config import Paths
from dlm_console.core import ApiError, Console
from dlm_console.server import ConsoleServer

STORE_SCOPE = "store:alice"
SHADOW_SCOPE = "shadow-alice"
EVENT_ID = "evt_alice_1"


def _create_store_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE dl_capture(
          seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
          event_hash TEXT NOT NULL, content TEXT NOT NULL, content_sha TEXT NOT NULL,
          recorded_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          linked_source TEXT, linked_title TEXT, UNIQUE(scope,event_hash));
        CREATE TABLE events(scope TEXT NOT NULL, id TEXT NOT NULL, title TEXT NOT NULL,
          PRIMARY KEY(scope,id));
        CREATE TABLE dl_import_provenance(scope TEXT, seq INTEGER, source_label TEXT,
          original_timestamp TEXT, imported_at TEXT);
        CREATE TABLE dl_suggestions(scope TEXT, capture_seq INTEGER);
    """)
    rows = [
        (STORE_SCOPE, "h1", "我喜欢吃苹果", "2026-11-10T10:00:00+00:00",
         "pending", None, None),
        (STORE_SCOPE, "h2", "你之前喜欢吃苹果吗", "2026-11-10T10:01:00+00:00",
         "pending", None, None),
        (STORE_SCOPE, "h3", "周末去公园跑步", "2020-01-01T00:00:00+00:00",
         "organized", "event-1", "公园跑步"),
    ]
    rows = [(scope, event_hash, content,
             hashlib.sha256(content.encode("utf-8")).hexdigest(),
             recorded_at, state, linked_source, linked_title)
            for (scope, event_hash, content, recorded_at, state,
                 linked_source, linked_title) in rows]
    con.executemany("INSERT INTO dl_capture(scope,event_hash,content,content_sha,"
                    "recorded_at,state,linked_source,linked_title) VALUES(?,?,?,?,?,?,?,?)",
                    rows)
    con.execute("INSERT INTO events VALUES(?,?,?)", (STORE_SCOPE, "event-1", "公园跑步"))
    con.execute("INSERT INTO dl_import_provenance VALUES(?,?,?,?,?)",
                (STORE_SCOPE, 3, "微信导入", "2020-01-01", "2026-11-01T00:00:00+00:00"))
    con.execute("INSERT INTO dl_suggestions VALUES(?,?)", (STORE_SCOPE, 1))
    con.commit()
    con.close()


def _create_shadow_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE event_candidate(event_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
          event_type TEXT NOT NULL, title TEXT NOT NULL, state TEXT NOT NULL,
          current_revision INTEGER NOT NULL, narrator TEXT NOT NULL DEFAULT 'unknown',
          subject TEXT NOT NULL DEFAULT 'unknown', reality_status TEXT NOT NULL DEFAULT 'unknown',
          story_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE event_revision(event_id TEXT NOT NULL, rev INTEGER NOT NULL,
          summary TEXT NOT NULL, status TEXT NOT NULL, modality TEXT NOT NULL,
          occurred_at TEXT, time_expression_raw TEXT, created_at TEXT NOT NULL,
          model_version TEXT NOT NULL DEFAULT '', prompt_version TEXT NOT NULL DEFAULT '',
          input_digest TEXT NOT NULL DEFAULT '', PRIMARY KEY(event_id, rev));
        CREATE TABLE event_evidence(event_id TEXT NOT NULL, rev INTEGER NOT NULL,
          scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL, quote TEXT NOT NULL,
          valid INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(event_id, rev, scope_id, source_seq, quote));
        CREATE TABLE source_receipt(scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          outcome TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
          PRIMARY KEY(scope_id, source_seq));
        CREATE TABLE memory_item(item_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
          event_id TEXT NOT NULL, item_type TEXT NOT NULL, text TEXT NOT NULL,
          modality TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE event_relation(id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope_id TEXT NOT NULL, from_event TEXT NOT NULL, to_event TEXT NOT NULL,
          relation_type TEXT NOT NULL, status TEXT NOT NULL, source_seq INTEGER,
          created_at TEXT NOT NULL);
        CREATE TABLE native_input_inbox(key TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
          scope_id TEXT NOT NULL, source_seq INTEGER, payload TEXT NOT NULL,
          state TEXT NOT NULL, UNIQUE(scope_id,source_seq));
        CREATE TABLE native_input_attempt(key TEXT PRIMARY KEY, epoch INTEGER NOT NULL,
          judge_revision TEXT NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE resolution_pending(pending_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL,
          source_seq INTEGER NOT NULL, proposals_json TEXT NOT NULL,
          mapping_json TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE validation_issue(id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id TEXT NOT NULL, op_index INTEGER NOT NULL, code TEXT NOT NULL,
          detail TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE event_link_decision(decision_id TEXT PRIMARY KEY,
          proposal_id TEXT NOT NULL, scope_id TEXT NOT NULL, source_seq INTEGER NOT NULL,
          event_id TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL);
    """)
    con.execute("INSERT INTO meta VALUES('schema_version','1')")
    con.execute("INSERT INTO event_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (EVENT_ID, SHADOW_SCOPE, "unknown", "公园跑步", "provisional", 1,
                 "user", "alice", "real", None,
                 "2024-01-01T10:00:00+00:00", "2024-01-01T10:00:00+00:00"))
    con.execute("INSERT INTO event_revision VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (EVENT_ID, 1, "周末去公园跑步", "active", "text", None, None,
                 "2024-01-01T10:00:00+00:00", "", "", ""))
    con.execute("INSERT INTO event_evidence VALUES(?,?,?,?,?,?)",
                (EVENT_ID, 1, SHADOW_SCOPE, 1, "周末去公园跑步", 1))
    con.execute("INSERT INTO source_receipt VALUES(?,?,?,?,?)",
                (SHADOW_SCOPE, 1, "CREATED", "sha1", "2026-11-10T10:00:00+00:00"))
    con.execute("INSERT INTO memory_item VALUES(?,?,?,?,?,?,?)",
                ("item-1", SHADOW_SCOPE, EVENT_ID, "episode", "周末去公园跑步", "text",
                 "2026-11-10T10:00:00+00:00"))
    con.execute("INSERT INTO event_relation(scope_id,from_event,to_event,relation_type,"
                "status,created_at) VALUES(?,?,?,?,?,?)",
                (SHADOW_SCOPE, EVENT_ID, "evt_other", "RELATED", "active",
                 "2026-11-10T10:00:00+00:00"))
    payload = json.dumps({"role": "user", "text": "我喜欢吃苹果", "recorded_at": None,
                          "stream_id": "stream-1", "identity_mode": "native_id"},
                        ensure_ascii=False)
    con.execute("INSERT INTO native_input_inbox VALUES(?,?,?,?,?,?)",
                ("k1", "sha1", SHADOW_SCOPE, 1, payload, "QUEUED"))
    con.execute("INSERT INTO native_input_inbox VALUES(?,?,?,?,?,?)",
                ("k2", "sha2", SHADOW_SCOPE, 2, payload, "DEFERRED"))
    con.execute("INSERT INTO native_input_attempt VALUES(?,?,?,?)",
                ("k2", 3, "native-input-0.2", "candidate_coverage"))
    con.execute("INSERT INTO resolution_pending VALUES(?,?,?,?,?,?,?,?,?)",
                ("p1", SHADOW_SCOPE, 2, "[]", "{}", "resolver_deferred", "PENDING",
                 "2026-11-10T10:02:00+00:00", "2026-11-10T10:02:00+00:00"))
    con.execute("INSERT INTO validation_issue(job_id,op_index,code,detail,created_at) "
                "VALUES(?,?,?,?,?)",
                ("job-1", 0, "BUDGET", "prompt too long", "2026-11-10T10:03:00+00:00"))
    con.execute("INSERT INTO event_link_decision VALUES(?,?,?,?,?,?,?,?)",
                ("d1", "proposal-1", SHADOW_SCOPE, 1, EVENT_ID, "CONFLICT", "different",
                 "2026-11-10T10:01:00+00:00"))
    con.execute("INSERT INTO event_link_decision VALUES(?,?,?,?,?,?,?,?)",
                ("d2", "proposal-2", SHADOW_SCOPE, 2, EVENT_ID, "ALREADY_ATTACHED", "",
                 "2026-11-10T10:04:00+00:00"))
    con.commit()
    con.close()


def _create_journal_db(path: Path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE native_stream(stream_id TEXT PRIMARY KEY, mode TEXT NOT NULL);
        CREATE TABLE native_record(key TEXT PRIMARY KEY, stream_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL,
          delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE import_attempt(id INTEGER PRIMARY KEY, stream_id TEXT NOT NULL,
          status TEXT NOT NULL, detail TEXT NOT NULL);
    """)
    con.execute("INSERT INTO native_stream VALUES('stream-1','native_id')")
    con.execute("INSERT INTO native_record VALUES('k1','stream-1',0,'h1','{}',1)")
    con.execute("INSERT INTO native_record VALUES('k2','stream-1',1,'h2','{}',0)")
    con.execute("INSERT INTO import_attempt VALUES(1,'stream-1','STAGED','ok')")
    con.execute("INSERT INTO import_attempt VALUES(2,'stream-1','REJECTED','ImportConflict:non-prefix')")
    con.commit()
    con.close()


def _write_logs(shadow_dir: Path, store_dir: Path):
    now = time.time()
    rows = [
        {"ts": now - 60, "scope": SHADOW_SCOPE, "query": "我喜欢吃什么",
         "items": 1, "event_ids": [EVENT_ID], "rendered_tokens": 120},
        {"ts": now - 30, "scope": SHADOW_SCOPE, "query": "周末做了什么",
         "items": 0, "event_ids": [], "rendered_tokens": 0},
        {"ts": now - 10, "scope": "shadow-bob", "query": "跑步",
         "items": 2, "event_ids": [EVENT_ID, "evt_2"], "rendered_tokens": 200,
         "output_status": "INJECTED", "budget_tokens": 512},
    ]
    (shadow_dir / "shadow_events.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")
    latency = [{"ts": now - 60, "ms": 5.0, "injected": True},
               {"ts": now - 50, "ms": 20.0, "injected": False},
               {"ts": now - 40, "ms": 300.0, "injected": True},
               {"ts": now - 30, "ms": 2000.0, "injected": True}]
    (store_dir / "latency.jsonl").write_text(
        "\n".join(json.dumps(row) for row in latency) + "\n", encoding="utf-8")


class ConsoleFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dlm-console-test-")
        root = Path(self.tmp.name)
        self.store_dir = root / "store"
        self.shadow_dir = root / "shadow"
        self.store_dir.mkdir()
        self.shadow_dir.mkdir()
        self.store_db = self.store_dir / "manual_test_memory.sqlite3"
        self.shadow_db = self.shadow_dir / "shadow.sqlite3"
        self.journal_db = self.shadow_dir / "native_input_journal.sqlite3"
        _create_store_db(self.store_db)
        _create_shadow_db(self.shadow_db)
        _create_journal_db(self.journal_db)
        _write_logs(self.shadow_dir, self.store_dir)
        self.paths = Paths(workspace=root, instance=None, plugin_data=None,
                           store_dir=self.store_dir, shadow_dir=self.shadow_dir,
                           token="", read_only=False)
        self.console = Console(self.paths)

    def tearDown(self):
        self.tmp.cleanup()

    def readonly(self):
        return Console(Paths(workspace=self.paths.workspace, instance=None,
                             plugin_data=None, store_dir=self.store_dir,
                             shadow_dir=self.shadow_dir, token="", read_only=True))


class TestOverview(ConsoleFixture):
    def test_counts_and_components(self):
        data = self.console.overview()
        self.assertEqual(data["counts"]["store"]["captures"], 3)
        self.assertEqual(data["counts"]["store"]["pending"], 2)
        self.assertEqual(data["counts"]["store"]["states"], {"pending": 2, "organized": 1})
        self.assertEqual(data["counts"]["shadow"]["events"], 1)
        self.assertEqual(data["counts"]["shadow"]["evidence"], 1)
        self.assertEqual(data["counts"]["shadow"]["deferred"], 1)
        self.assertEqual(data["counts"]["journal"]["records"], 2)
        self.assertEqual(data["counts"]["journal"]["undelivered"], 1)
        self.assertEqual(data["counts"]["journal"]["rejected"], 1)
        self.assertEqual(data["counts"]["observations"]["loaded"], 3)
        self.assertEqual(data["engine"]["schema_version"], "1")
        self.assertEqual(len(data["activity"]), 14)
        self.assertFalse(data["capacity"]["sample_sufficient"])
        self.assertEqual(data["capacity"]["per_message_bytes"], 11 * 1024)
        self.assertIn("100", data["capacity"]["projections_5y"])
        levels = {item["level"] for item in data["warnings"]}
        self.assertIn("warn", levels)
        self.assertTrue(any("备份" in item["text"] for item in data["warnings"]))

    def test_environment(self):
        data = self.console.environment()
        labels = {item["label"] for item in data["paths"]}
        self.assertIn("基座数据库", labels)
        self.assertIn("store", data["tables"])
        self.assertIn("shadow", data["tables"])
        self.assertTrue(data["output"]["available"])
        self.assertEqual(data["output"]["schema_version"], "memory-output-v1")
        self.assertFalse(data["app"]["read_only"])


class TestStore(ConsoleFixture):
    def test_scopes_and_filters(self):
        scopes = self.console.store_scopes()["items"]
        self.assertEqual(scopes[0]["captures"], 3)
        page = self.console.store_captures(q="苹果", size=10)
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["items"][0]["seq"], 2)
        page = self.console.store_captures(state="organized")
        self.assertEqual(page["total"], 1)

    def test_detail_and_provenance(self):
        row = self.console.store_capture(STORE_SCOPE, 3)
        self.assertEqual(row["content"], "周末去公园跑步")
        self.assertEqual(row["linked_title"], "公园跑步")
        self.assertEqual(row["provenance"]["source_label"], "微信导入")
        with self.assertRaises(ApiError):
            self.console.store_capture(STORE_SCOPE, 999)

    def test_delete_single_with_backup(self):
        backup = self.console.backup("store")
        self.assertTrue((self.store_dir / "backups" / backup["name"]).is_file())
        result = self.console.store_delete(STORE_SCOPE, 1, False, True)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(self.console.store_captures()["total"], 2)
        with closing(sqlite3.connect(self.store_db)) as con:
            self.assertEqual(con.execute(
                "SELECT COUNT(*) FROM dl_suggestions WHERE capture_seq=1").fetchone()[0], 0)

    def test_delete_scope(self):
        result = self.console.store_delete(STORE_SCOPE, None, True, True)
        self.assertEqual(result["deleted"], 3)
        with closing(sqlite3.connect(self.store_db)) as con:
            for table in ("dl_capture", "events", "dl_suggestions"):
                self.assertEqual(con.execute(
                    "SELECT COUNT(*) FROM %s WHERE scope=?" % table,
                    (STORE_SCOPE,)).fetchone()[0], 0)

    def test_readonly_rejects_mutation(self):
        with self.assertRaises(ApiError) as ctx:
            self.readonly().store_delete(STORE_SCOPE, 1, False, False)
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError):
            self.readonly().backup("store")


class TestShadow(ConsoleFixture):
    def test_events_search_and_detail(self):
        page = self.console.shadow_events(q="公园")
        self.assertEqual(page["total"], 1)
        item = page["items"][0]
        self.assertEqual(item["event_id"], EVENT_ID)
        self.assertEqual(item["evidence"], 1)
        detail = self.console.shadow_event(EVENT_ID)
        self.assertEqual(len(detail["evidence"]), 1)
        self.assertEqual(len(detail["relations"]), 1)
        self.assertEqual(len(detail["items"]), 1)

    def test_inbox(self):
        data = self.console.shadow_inbox()
        self.assertTrue(data["available"])
        self.assertEqual(data["states"]["QUEUED"], 1)
        self.assertEqual(data["states"]["DEFERRED"], 1)
        deferred = self.console.shadow_inbox(state="DEFERRED")["items"][0]
        self.assertEqual(deferred["reason"], "candidate_coverage")
        self.assertEqual(deferred["text"], "我喜欢吃苹果")

    def test_conflicts(self):
        data = self.console.shadow_conflicts()
        kinds = [item["kind"] for item in data["items"]]
        self.assertIn("resolution_pending", kinds)
        self.assertIn("validation_issue", kinds)
        self.assertIn("link_decision", kinds)
        self.assertIn("import_rejected", kinds)
        # ALREADY_ATTACHED 是正常结果，不是冲突。
        decisions = [item for item in data["items"] if item["kind"] == "link_decision"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "CONFLICT")
        rejected = [item for item in data["items"] if item["kind"] == "import_rejected"]
        self.assertIsNone(rejected[0]["created_at"])


class TestObservations(ConsoleFixture):
    def test_observation_filters(self):
        data = self.console.observations()
        self.assertEqual(data["total"], 3)
        self.assertEqual(self.console.observations(scope=SHADOW_SCOPE)["total"], 2)
        self.assertEqual(self.console.observations(q="跑步")["total"], 1)
        self.assertEqual(self.console.observations(days=1)["total"], 3)

    def test_summary(self):
        summary = self.console.observations_summary()
        self.assertEqual(summary["loaded"], 3)
        self.assertEqual(summary["injected"], 2)
        self.assertAlmostEqual(summary["inject_rate"], 0.667, places=3)
        self.assertEqual(summary["histogram"]["1"], 1)
        self.assertEqual(summary["outputs"]["INJECTED"], 1)
        self.assertEqual(summary["outputs"]["OBSERVE"], 2)

    def test_latency(self):
        data = self.console.latency()
        self.assertTrue(data["available"])
        self.assertEqual(data["turns"], 4)
        self.assertEqual(data["injected"], 3)
        self.assertEqual(data["max"], 2000.0)
        self.assertEqual(data["buckets"]["1000ms+"], 1)


class TestMaintenance(ConsoleFixture):
    def test_backup_rotate_checkpoint_integrity_vacuum(self):
        first = self.console.backup("store")
        second = self.console.backup("store")
        self.assertNotEqual(first["name"], second["name"])
        self.assertTrue(first["name"].startswith("console_store_"))
        backups = self.console.backups("store")
        self.assertEqual(backups["total"], 2)
        rotated = self.console.rotate("store", keep=1)
        self.assertEqual(rotated["kept"], 1)
        self.assertEqual(len(rotated["removed"]), 1)
        self.assertEqual(self.console.backups("store")["total"], 1)
        self.assertFalse(self.console.checkpoint("store")["busy"])
        self.assertTrue(self.console.integrity("store")["ok"])
        result = self.console.vacuum("store")
        self.assertIn("before_bytes", result)

    def test_rotate_ignores_non_backup_files(self):
        (self.store_dir / "backups").mkdir(exist_ok=True)
        (self.store_dir / "backups" / "notes.txt").write_text("keep", encoding="utf-8")
        self.console.backup("store")
        self.console.rotate("store", keep=1)
        self.assertTrue((self.store_dir / "backups" / "notes.txt").is_file())

    def test_verify_backup(self):
        backed = self.console.backup("store")
        report = self.console.verify_backup("store", backed["name"])
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["counts"]["captures"], 3)
        self.assertEqual(report["counts"]["scopes"], 1)
        self.assertTrue(report["checks"]["capture_hashes"])
        self.assertTrue(report["checks"]["foreign_keys"])

    def test_verify_journal_backup(self):
        backed = self.console.backup("journal")
        report = self.console.verify_backup("journal", backed["name"])
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["counts"]["records"], 2)
        self.assertEqual(report["counts"]["undelivered"], 1)

    def test_verify_corrupt_backup(self):
        folder = self.store_dir / "backups"
        folder.mkdir(exist_ok=True)
        (folder / "broken.sqlite3").write_bytes(b"this is not a sqlite database")
        report = self.console.verify_backup("store", "broken.sqlite3")
        self.assertFalse(report["ok"])
        self.assertTrue(any("database" in error for error in report["errors"]), report)

    def test_backup_name_traversal_rejected(self):
        with self.assertRaises(ApiError):
            self.console.verify_backup("store", "../outside.sqlite3")
        with self.assertRaises(ApiError):
            self.console.restore_backup("store", "..\\outside.sqlite3")

    def test_restore_creates_new_copy(self):
        backed = self.console.backup("store")
        first = self.console.restore_backup("store", backed["name"])
        target = Path(first["target"])
        self.assertTrue(target.is_file())
        self.assertEqual(target.parent, self.store_dir / "restores")
        self.assertEqual(first["counts"]["captures"], 3)
        self.assertIn("未替换", first["note"])
        # 控制台自己的库仍保持原样。
        self.assertEqual(self.console.store_captures()["total"], 3)
        second = self.console.restore_backup("store", backed["name"])
        self.assertNotEqual(first["target"], second["target"])

    def test_readonly_can_verify_but_not_restore(self):
        backed = self.console.backup("store")
        readonly = self.readonly()
        self.assertTrue(readonly.verify_backup("store", backed["name"])["ok"])
        with self.assertRaises(ApiError) as ctx:
            readonly.restore_backup("store", backed["name"])
        self.assertEqual(ctx.exception.status, 403)

    def test_archive_captures(self):
        preview = self.console.archive(months=12, export=False, kind="captures")
        self.assertEqual(preview["count"], 1)
        exported = self.console.archive(months=12, export=True, kind="captures")
        self.assertTrue(exported["exported"])
        path = Path(exported["path"])
        self.assertTrue(path.is_file())
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["content"], "周末去公园跑步")
        self.assertEqual(rows[0]["linked_title"], "公园跑步")

    def test_archive_events(self):
        exported = self.console.archive(months=12, export=True, kind="events")
        self.assertEqual(exported["count"], 1)
        path = Path(exported["path"])
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["event_id"], EVENT_ID)
        self.assertEqual(len(rows[0]["evidence"]), 1)

    def test_audit_records_actions(self):
        self.console.backup("store")
        items = self.console.audit()["items"]
        self.assertTrue(any(item["action"] == "backup" for item in items))


class TestServer(ConsoleFixture):
    def setUp(self):
        super().setUp()
        self.server = ConsoleServer(("127.0.0.1", 0), self.console, "secret-token")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, path, method="GET", body=None, headers=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data,
            method=method, headers=headers or {})
        return urllib.request.urlopen(request, timeout=5)

    def test_static_page(self):
        with self.request("/") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("数字记忆", response.read().decode("utf-8"))

    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request("/api/overview")
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 401)
        with self.request("/api/overview", headers={"X-Console-Token": "secret-token"}) as response:
            self.assertEqual(response.status, 200)
            data = json.loads(response.read().decode("utf-8"))
        self.assertEqual(data["counts"]["store"]["captures"], 3)

    def test_token_in_query(self):
        with self.request("/api/health?token=secret-token") as response:
            self.assertEqual(response.status, 200)

    def test_post_requires_json_and_same_origin(self):
        headers = {"X-Console-Token": "secret-token"}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request("/api/maintenance/backup", method="POST", body={"dataset": "store"},
                         headers=headers)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 415)
        headers = {"X-Console-Token": "secret-token", "Content-Type": "application/json",
                   "Origin": "http://evil.example"}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request("/api/maintenance/backup", method="POST", body={"dataset": "store"},
                         headers=headers)
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 403)
        headers = {"X-Console-Token": "secret-token", "Content-Type": "application/json",
                   "Origin": "http://127.0.0.1:%d" % self.port}
        with self.request("/api/maintenance/backup", method="POST",
                          body={"dataset": "store"}, headers=headers) as response:
            self.assertEqual(response.status, 200)
            data = json.loads(response.read().decode("utf-8"))
        self.assertTrue(data["name"].startswith("console_store_"))

    def test_unknown_api(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request("/api/nope", headers={"X-Console-Token": "secret-token"})
        ctx.exception.close()
        self.assertEqual(ctx.exception.code, 404)

    def test_verify_backup_api(self):
        backup = self.console.backup("store")
        headers = {"X-Console-Token": "secret-token",
                   "Content-Type": "application/json"}
        with self.request("/api/maintenance/verify_backup", method="POST",
                          body={"dataset": "store", "name": backup["name"]},
                          headers=headers) as response:
            self.assertEqual(response.status, 200)
            data = json.loads(response.read().decode("utf-8"))
        self.assertTrue(data["ok"])
        self.assertEqual(data["counts"]["captures"], 3)


if __name__ == "__main__":
    unittest.main()
