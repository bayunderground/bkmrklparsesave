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
        # First field stays empty (so the hash fallback kicks in); the keys
        # after it are reordered — canonical JSON must give the same marker.
        one = self.save(
            {"rule": "order", "items": [{"fields": {"A": "", "B": "", "C": "v"}}]}
        )
        two = self.save(
            {"rule": "order", "items": [{"fields": {"A": "", "C": "v", "B": ""}}]}
        )
        self.assertEqual((one[1]["saved"], one[1]["skipped"]), (1, 0))
        self.assertEqual((two[1]["saved"], two[1]["skipped"]), (0, 1))

    def test_hash_fallback_identical_fields_skip(self):
        self.save(
            {"rule": "cannon", "items": [{"fields": {"A": "", "B": "", "X": "1", "Y": "2"}}]}
        )
        status, body = self.save(
            {"rule": "cannon", "items": [{"fields": {"A": "", "Y": "2", "X": "1", "B": ""}}]}
        )
        self.assertEqual((body["saved"], body["skipped"]), (0, 1))


class MarkdownSafetyTest(ServerTestCase):
    def test_unicode_and_multiline_survive(self):
        self.save(
            {
                "rule": "uni",
                "items": [
                    {
                        "fields": {
                            "Title": "Kōji Soy Sauce 🌈",
                            "Description": "line one\nline two\n\nafter blank",
                            "Price": 9.0,
                            "Active": True,
                            "Broken": False,
                        }
                    }
                ],
            }
        )
        text = self.read_md("uni")
        self.assertIn("## Kōji Soy Sauce 🌈", text)
        self.assertIn("  line one\n  line two\n  \n  after blank", text)
        self.assertIn("- Price: 9\n", text)
        self.assertIn("- Active: Yes", text)
        self.assertIn("- Broken: No", text)

    def test_no_forged_marker_or_sections_from_values(self):
        evil = {
            "rule": "evil",
            "items": [
                {
                    "fields": {
                        "Title": "## Hacked Title",
                        "Line": "---",
                        "Marker": "<!-- item-id: 0123456789abcdef -->",
                        "Block": (
                            "before\n"
                            "<!-- item-id: fedcba9876543210 -->\n"
                            "---\n"
                            "## Fake Section"
                        ),
                    }
                }
            ],
        }
        self.save(evil)
        text = self.read_md("evil")
        # Only the real marker, at column 0, exactly once.
        real = [
            line
            for line in text.split("\n")
            if line.startswith("<!-- item-id:")
        ]
        self.assertEqual(len(real), 1)
        self.assertRegex(real[0], r"^<!-- item-id: [0-9a-f]{16} -->$")
        # The forged IDs may appear as text, but never as a real marker line.
        marker_lines = [
            l for l in text.split("\n") if l.startswith("<!-- item-id:")
        ]
        self.assertNotIn("<!-- item-id: 0123456789abcdef -->", marker_lines)
        self.assertNotIn("<!-- item-id: fedcba9876543210 -->", marker_lines)
        self.assertIn("&lt;!-- item-id: 0123456789abcdef -->", text)
        # No forged section: exactly one '## ' heading.
        headings = [l for l in text.split("\n") if l.startswith("## ")]
        self.assertEqual(headings, ["## Hacked Title"])
        # The embedded --- sits indented inside the value, not as a section rule.
        rules = [l for l in text.split("\n") if l == "---"]
        self.assertEqual(len(rules), 1)

    def test_label_cannot_break_structure(self):
        self.save(
            {
                "rule": "label",
                "items": [{"fields": {"Evil\nLabel: ---": "v", "B": "w"}}],
            }
        )
        text = self.read_md("label")
        self.assertEqual([l for l in text.split("\n") if l == "---"], ["---"])
        headings = [l for l in text.split("\n") if l.startswith("## ")]
        self.assertEqual(headings, ["## v"])

    def test_multiline_values_never_at_column_zero(self):
        self.save(
            {
                "rule": "col",
                "items": [
                    {"fields": {"T": "t", "V": "safe\n---\n## nope\n<!-- item-id: aaaaaaaaaaaaaaaa -->"}}
                ],
            }
        )
        text = self.read_md("col")
        for line in text.split("\n")[3:-1]:
            if line in ("", "---") or line.startswith(("- ", "<!--")):
                continue
            self.assertTrue(line.startswith("  "), repr(line))


class ValidationTest(ServerTestCase):
    def test_invalid_rule_names_rejected_and_nothing_written(self):
        before_data = sorted(p.name for p in self.data.iterdir())
        before_parent = sorted(p.name for p in self.data.parent.iterdir())
        for bad in ["../x", "a/b", "A", "", "x" * 65, ".hidden", "x.md", "a b", "a.b"]:
            status, body = self.save({"rule": bad, "items": [{"fields": {"T": "v"}}]})
            self.assertEqual(status, 400, (bad, status, body))
            self.assertFalse(body["ok"])
            self.assertIsInstance(body["error"], str)
        # Nothing created, inside the data dir or anywhere next to it.
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), before_data)
        self.assertEqual(
            sorted(p.name for p in self.data.parent.iterdir()), before_parent
        )

    def test_valid_boundary_rule_names_accepted(self):
        for good in ["a", "0", "products", "a" * 64, "a-b_c-9"]:
            status, body = self.save({"rule": good, "items": [{"fields": {"T": "v"}}]})
            self.assertEqual(status, 200, (good, status, body))
            self.assertTrue((self.data / f"{good}.md").is_file())

    def test_malformed_and_wrong_shape_bodies(self):
        cases = [
            (b"{not json", "application/json"),
            (b"", "application/json"),
            (b"\xff\xfe", "application/json"),
            (b'{"rule":"ok","items":[{"fields":{"T":NaN}}]}', "application/json"),
        ]
        for raw, ctype in cases:
            status, body = self.save(None, raw=raw, ctype=ctype)
            self.assertEqual(status, 400, (raw, status, body))
            self.assertFalse(body["ok"])

        wrong = [
            ("text/plain", b"{}"),
            ("application/xml", b"{}"),
        ]
        for ctype, raw in wrong:
            status, body = self.save(None, raw=raw, ctype=ctype)
            self.assertEqual(status, 415, (ctype, status, body))

    def test_wrong_types_rejected(self):
        payloads = [
            ["not", "an", "object"],
            {"items": [{"fields": {"T": "v"}}]},                      # missing rule
            {"rule": "ok", "items": "nope"},
            {"rule": "ok", "items": []},
            {"rule": "ok", "items": ["string"]},
            {"rule": "ok", "items": [{"fields": {}}]},
            {"rule": "ok", "items": [{"fields": "str"}]},
            {"rule": "ok", "items": [{"fields": {"T": {"nested": 1}}}]},
            {"rule": "ok", "items": [{"fields": {"T": [1, {"a": 1}]}}]},
            {"rule": "ok", "items": [{"id": 42, "fields": {"T": "v"}}]},
            {"rule": "ok", "url": 123, "items": [{"fields": {"T": "v"}}]},
            {"rule": "ok", "items": [{"fields": {"T": None}}]},        # null-only
        ]
        for payload in payloads:
            status, body = self.save(payload)
            self.assertEqual(status, 400, (payload, status, body))
            self.assertFalse(body["ok"])
            self.assertNotIn("Traceback", json.dumps(body))

    def test_item_count_limit(self):
        items = [{"fields": {"T": str(i)}} for i in range(1001)]
        status, body = self.save({"rule": "big", "items": items})
        self.assertEqual(status, 400)
        self.assertFalse((self.data / "big.md").exists())

    def test_oversized_body_rejected(self):
        status, body = self.save(
            None,
            raw=b"X" * (5 * 1024 * 1024 + 1),
            ctype="application/json",
        )
        self.assertEqual(status, 413)
        self.assertFalse(body["ok"])


class ConcurrencyTest(ServerTestCase):
    def test_twenty_threads_write_each_id_once(self):
        import concurrent.futures

        items = [{"fields": {"Title": f"Item {i}", "Price": i}} for i in range(5)]

        def post(_):
            status, body = self.save({"rule": "race", "items": items})
            self.assertEqual(status, 200)
            return body

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(post, range(20)))

        saved_total = sum(r["saved"] for r in results)
        self.assertEqual(saved_total, 5)  # across 20 posts, only 5 new

        text = self.read_md("race")
        markers = [l for l in text.split("\n") if l.startswith("<!-- item-id:")]
        self.assertEqual(len(markers), 5)
        self.assertEqual(len(set(markers)), 5)  # each exactly once
        # Sections intact: every heading present exactly once, file well-formed.
        for i in range(5):
            self.assertEqual(text.count(f"## Item {i}\n"), 1)
        self.assertTrue(text.endswith("---\n"))
        parts = text.split("\n\n## ")
        self.assertEqual(len(parts), 5)  # 5 sections joined by blank lines

    def test_interleaved_different_rules_do_not_interfere(self):
        import concurrent.futures

        def post(rule, title):
            status, _ = self.save({"rule": rule, "items": [{"fields": {"T": title}}]})
            self.assertEqual(status, 200)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            jobs = []
            for i in range(10):
                jobs.append(pool.submit(post, "alpha", f"A{i}"))
                jobs.append(pool.submit(post, "beta", f"B{i}"))
            for job in jobs:
                job.result()

        self.assertEqual(self.read_md("alpha").count("<!-- item-id: "), 10)
        self.assertEqual(self.read_md("beta").count("<!-- item-id: "), 10)


if __name__ == "__main__":
    unittest.main()
