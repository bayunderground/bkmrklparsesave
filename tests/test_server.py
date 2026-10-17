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


class SaveDedupeTest(ServerTestCase):
    def test_creates_file_and_appends(self):
        status, body = self.save(
            {
                "rule": "products",
                "url": "https://example.com/shop/page/2",
                "items": [
                    {"id": "SKU-1", "fields": {"Title": "First", "Price": 10}},
                    {"fields": {"Title": "Second", "Price": 20}},
                ],
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            body, {"ok": True, "rule": "products", "file": "products.md",
                   "saved": 2, "skipped": 0}
        )
        text = self.read_md("products")
        self.assertIn("## First", text)
        self.assertIn("## Second", text)

        status, body = self.save(
            {"rule": "products", "items": [{"fields": {"Title": "Third"}}]}
        )
        self.assertEqual(status, 200)
        self.assertEqual((body["saved"], body["skipped"]), (1, 0))
        self.assertIn("## Third", self.read_md("products"))

    def test_repeat_submission_skips_everything(self):
        payload = {
            "rule": "shop",
            "items": [
                {"id": "A", "fields": {"Title": "Alpha"}},
                {"fields": {"Title": "Beta"}},
            ],
        }
        self.save(payload)
        status, body = self.save(payload)
        self.assertEqual(status, 200)
        self.assertEqual((body["saved"], body["skipped"]), (0, 2))
        text = self.read_md("shop")
        self.assertEqual(text.count("<!-- item-id: "), 2)

    def test_partial_overlap_skips_only_duplicates(self):
        self.save({"rule": "mix", "items": [{"fields": {"Title": "One"}}]})
        status, body = self.save(
            {
                "rule": "mix",
                "items": [
                    {"fields": {"Title": "One"}},
                    {"fields": {"Title": "Two"}},
                ],
            }
        )
        self.assertEqual((body["saved"], body["skipped"]), (1, 1))
        self.assertEqual(self.read_md("mix").count("<!-- item-id: "), 2)

    def test_duplicate_within_one_request(self):
        status, body = self.save(
            {
                "rule": "dup",
                "items": [
                    {"fields": {"Title": "Same"}},
                    {"fields": {"Title": "Same"}},
                ],
            }
        )
        self.assertEqual((body["saved"], body["skipped"]), (1, 1))
        self.assertEqual(self.read_md("dup").count("## Same"), 1)


class IdFallbackTest(ServerTestCase):
    def test_explicit_id_wins_even_when_titles_match(self):
        self.save(
            {
                "rule": "ids",
                "items": [{"id": "SKU-1", "fields": {"Title": "Same"}}],
            }
        )
        status, body = self.save(
            {"rule": "ids", "items": [{"id": "SKU-2", "fields": {"Title": "Same"}}]}
        )
        self.assertEqual((body["saved"], body["skipped"]), (1, 0))
        self.assertEqual(self.read_md("ids").count("## Same"), 2)

    def test_explicit_id_strips_whitespace(self):
        self.save({"rule": "trim", "items": [{"id": "  ABC  ", "fields": {"T": "x"}}]})
        status, body = self.save(
            {"rule": "trim", "items": [{"id": "ABC", "fields": {"T": "y"}}]}
        )
        self.assertEqual((body["saved"], body["skipped"]), (0, 1))

    def test_first_field_fallback(self):
        self.save({"rule": "first", "items": [{"fields": {"Title": "Hero", "Price": 1}}]})
        status, body = self.save(
            {"rule": "first", "items": [{"fields": {"Title": "Hero", "Price": 999}}]}
        )
        # Same first field -> same identifier -> skipped, per the documented caveat.
        self.assertEqual((body["saved"], body["skipped"]), (0, 1))

    def test_first_field_collision_is_the_documented_caveat(self):
        """Different items sharing the first-field value count as duplicates."""
        self.save({"rule": "coll", "items": [{"fields": {"Title": "Widget", "Price": 5}}]})
        status, body = self.save(
            {"rule": "coll", "items": [{"fields": {"Title": "Widget", "Price": 77}}]}
        )
        self.assertEqual((body["saved"], body["skipped"]), (0, 1))
        self.assertEqual(self.read_md("coll").count("<!-- item-id: "), 1)

    def test_hash_fallback_when_first_field_empty(self):
        a = self.save(
            {"rule": "hash", "items": [{"fields": {"A": "", "B": "z", "C": "1"}}]}
        )
        b = self.save(
            {"rule": "hash", "items": [{"fields": {"A": "", "B": "z", "C": "2"}}]}
        )
        self.assertEqual((a[1]["saved"], a[1]["skipped"]), (1, 0))
        self.assertEqual((b[1]["saved"], b[1]["skipped"]), (1, 0))

    def test_hash_fallback_is_key_order_independent(self):
        one = self.save(
            {"rule": "order", "items": [{"fields": {"A": "", "B": "", "C": "v"}}]}
        )
        two = self.save(
            {"rule": "order", "items": [{"fields": {"C": "v", "B": "", "A": ""}}]}
        )
        self.assertEqual((one[1]["saved"], one[1]["skipped"]), (1, 0))
        self.assertEqual((two[1]["saved"], two[1]["skipped"]), (0, 1))

    def test_hash_fallback_identical_fields_skip(self):
        self.save(
            {"rule": "cannon", "items": [{"fields": {"A": "", "B": "", "X": "1", "Y": "2"}}]}
        )
        status, body = self.save(
            {"rule": "cannon", "items": [{"fields": {"Y": "2", "X": "1", "B": "", "A": ""}}]}
        )
        self.assertEqual((body["saved"], body["skipped"]), (0, 1))


if __name__ == "__main__":
    unittest.main()
