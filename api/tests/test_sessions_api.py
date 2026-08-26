import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import ServerApi  # noqa: E402
from server import sessions  # noqa: E402


class FakeServitorServer:
    def __init__(self, name, client_ip):
        self.name = name

    async def check_due_reminders(self):
        return []


class SessionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = Path(self.tmp.name) / "tasks.db"

        for patcher in (
            patch.object(sessions, "DB_PATH", db),
            patch.object(ServerApi, "DB_PATH", db),
            patch.object(ServerApi, "ServitorServer", FakeServitorServer),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.db = db
        self.client = TestClient(ServerApi.app)

    def test_lifespan_creates_schema(self):
        with self.client:
            res = self.client.get("/sessions")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["sessions"], [])
        self.assertIsNotNone(body["active_id"])

    def test_session_crud_and_isolation(self):
        with self.client:
            first = self.client.post("/sessions", json={"title": "Primeira"}).json()
            second = self.client.post("/sessions").json()

            sessions.save_message("user", "oi", first["id"])
            sessions.save_message("assistant", "salve", first["id"])
            sessions.save_message("user", "outra conversa", second["id"])

            listed = self.client.get("/sessions").json()
            by_id = {s["id"]: s for s in listed["sessions"]}
            self.assertEqual(by_id[first["id"]]["message_count"], 2)
            self.assertEqual(by_id[second["id"]]["message_count"], 1)
            # creating a session activates it
            self.assertEqual(listed["active_id"], second["id"])

            convo = self.client.get(f"/conversation?session_id={first['id']}").json()
            self.assertEqual([m["content"] for m in convo["messages"]], ["oi", "salve"])

            # a session titled by hand keeps its title
            self.assertEqual(by_id[first["id"]]["title"], "Primeira")
            # an untitled one is named after its first user message
            self.assertEqual(by_id[second["id"]]["title"], "outra conversa")

            renamed = self.client.patch(f"/sessions/{second['id']}", json={"title": "Renomeada"})
            self.assertEqual(renamed.json()["title"], "Renomeada")

            activated = self.client.post(f"/sessions/{first['id']}/activate")
            self.assertEqual(activated.json()["active_id"], first["id"])
            # /conversation with no session id follows the active session
            self.assertEqual(
                self.client.get("/conversation").json()["session_id"], first["id"]
            )

            deleted = self.client.delete(f"/sessions/{first['id']}")
            self.assertEqual(deleted.json()["status"], "deleted")
            self.assertEqual(deleted.json()["active_id"], second["id"])

            conn = sqlite3.connect(self.db)
            left = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (first["id"],)
            ).fetchone()[0]
            conn.close()
            self.assertEqual(left, 0, "deleting a session must delete its messages")

    def test_missing_session_returns_404(self):
        with self.client:
            self.assertEqual(self.client.delete("/sessions/999").status_code, 404)
            self.assertEqual(self.client.post("/sessions/999/activate").status_code, 404)
            self.assertEqual(
                self.client.patch("/sessions/999", json={"title": "x"}).status_code, 404
            )

    def test_clear_conversation_only_clears_one_session(self):
        with self.client:
            a = self.client.post("/sessions").json()
            b = self.client.post("/sessions").json()
            sessions.save_message("user", "mensagem A", a["id"])
            sessions.save_message("user", "mensagem B", b["id"])

            self.client.delete(f"/conversation?session_id={a['id']}")

            self.assertEqual(self.client.get(f"/conversation?session_id={a['id']}").json()["messages"], [])
            kept = self.client.get(f"/conversation?session_id={b['id']}").json()["messages"]
            self.assertEqual([m["content"] for m in kept], ["mensagem B"])

    def test_pre_session_messages_are_migrated(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at DATETIME NOT NULL)"
        )
        conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES ('user', 'antigo', '2026-08-01 10:00:00')"
        )
        conn.commit()
        conn.close()

        with self.client:
            listed = self.client.get("/sessions").json()["sessions"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["message_count"], 1)
        self.assertEqual(listed[0]["title"], "Conversa anterior")


class ContextUsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = Path(self.tmp.name) / "tasks.db"
        for patcher in (
            patch.object(sessions, "DB_PATH", db),
            patch.object(ServerApi, "DB_PATH", db),
            patch.object(ServerApi, "ServitorServer", FakeServitorServer),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(ServerApi.app)

    def test_context_usage_reports_real_counts(self):
        fake = Mock()
        fake.agent = Mock(last_usage={"input_tokens": 4321})
        fake.context_usage = Mock(return_value={
            "session_id": 1, "used_tokens": 4321, "output_tokens": 88,
            "max_tokens": 32768, "reserved_tokens": 5000,
            "model": "gemma4:e2b-it-qat", "source": "last_turn", "exact": True,
        })
        with self.client, patch.object(ServerApi, "Servitor", fake):
            body = self.client.get("/context_usage").json()
        self.assertEqual(body["used_tokens"], 4321)
        self.assertEqual(body["source"], "last_turn")
        self.assertTrue(body["exact"])

    def test_refresh_drops_cached_turn_usage(self):
        fake = Mock()
        fake.agent = Mock(last_usage={"input_tokens": 4321})
        fake.context_usage = Mock(return_value={
            "session_id": 1, "used_tokens": 120, "output_tokens": 0,
            "max_tokens": 32768, "reserved_tokens": 5000,
            "model": "gemma4:e2b-it-qat", "source": "tokenizer", "exact": True,
        })
        with self.client, patch.object(ServerApi, "Servitor", fake):
            body = self.client.get("/context_usage?refresh=true").json()
        self.assertIsNone(fake.agent.last_usage)
        self.assertEqual(body["source"], "tokenizer")


if __name__ == "__main__":
    unittest.main()
