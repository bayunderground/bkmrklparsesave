"""Server tests: real server on a random port, temp data dir, stdlib only."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="bkmrkl-test-")
        server.DATA_DIR = Path(cls._tmp)
        server.Handler.log_message = lambda *a: None  # keep test output clean
        cls._srv = server.make_server(port=0)
        cls.port = cls._srv.server_address[1]
        cls._thread = threading.Thread(target=cls._srv.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown()
        cls._srv.server_close()
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def setUp(self):
        # Fresh data dir per test so files never leak between tests.
        self.data = Path(tempfile.mkdtemp(dir=self._tmp))
        server.DATA_DIR = self.data

    def save(self, payload, raw=None, ctype="application/json", path="/save"):
        """POST /save (or raw bytes); returns (status_code, parsed_json)."""
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={"Content-Type": ctype},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def get(self, path="/health"):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.status, json.loads(resp.read())

    def read_md(self, rule):
        return (self.data / f"{rule}.md").read_text(encoding="utf-8")


class HarnessTest(ServerTestCase):
    def test_health(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})


if __name__ == "__main__":
    unittest.main()
