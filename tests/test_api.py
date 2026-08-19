import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from listen_app import main
from listen_app.core import SessionStore


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.original_store = main.store
        self.temp_dir = tempfile.TemporaryDirectory()
        main.store = SessionStore(Path(self.temp_dir.name))
        main.runtime.runners.clear()
        main.runtime.sockets.clear()

    def tearDown(self):
        for runner in list(main.runtime.runners.values()):
            runner.stop()
        main.runtime.runners.clear()
        main.runtime.sockets.clear()
        main.store = self.original_store
        self.temp_dir.cleanup()

    def test_readiness_and_invalid_session_routes(self):
        with TestClient(main.app) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertTrue(health.json()["offline"])
            self.assertEqual(client.get("/api/sessions/not-safe").status_code, 400)
            self.assertEqual(client.get("/api/sessions/../../outside").status_code, 404)

    def test_websocket_receives_snapshot_and_stop_persists(self):
        with TestClient(main.app) as client:
            response = client.post(
                "/api/sessions/start",
                json={"title": "WebSocket test", "model": "whisper-small-int8"},
            )
            self.assertEqual(response.status_code, 200)
            session_id = response.json()["session"]["id"]
            with client.websocket_connect(f"/ws/{session_id}") as websocket:
                snapshot = websocket.receive_json()
                self.assertEqual(snapshot["type"], "session_snapshot")
                self.assertEqual(snapshot["session"]["id"], session_id)
            stopped = client.post(f"/api/sessions/{session_id}/stop")
            self.assertEqual(stopped.status_code, 200)
            self.assertIsNotNone(stopped.json()["session"]["ended_at"])


if __name__ == "__main__":
    unittest.main()
