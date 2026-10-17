"""Images for Jev (TypeSafe's "Dreamer-ready" preview) and the cloud half of the vision shadow.

* images.py refuses, before any request, every picture the API would refuse.
* client.ask sends assets and box questions to TypeSafe only, with the Danger Zone header,
  refuses an unreferenced asset, and validates a box answer like any other.
* `/jev vision_cloud shadow` is its own switch: the local shadow never sends a picture, and
  the cloud one logs ids, hashes and classes, never the picture, path or question.
"""
from __future__ import annotations

import base64
import http.server
import json
import os
import struct
import sys
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vision_shadow import QUESTION, Base  # noqa: E402

from jevkit import client, images  # noqa: E402


def png(width: int = 300, height: int = 300, animated: bool = False) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
    if animated:
        out += chunk(b"acTL", struct.pack(">II", 2, 0))
    return out + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def jpeg(width: int = 400, height: int = 300, marker: int = 0xC0) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = bytes([0xFF, marker]) + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


class Images(unittest.TestCase):
    def test_headers_are_read(self):
        self.assertEqual(images.inspect(png(320, 280)), ("png", 320, 280))
        self.assertEqual(images.inspect(jpeg(400, 300)), ("jpeg", 400, 300))

    def test_what_the_api_would_refuse_is_refused_here(self):
        cases = {"unsupported_format": b"GIF89a" + b"\x00" * 40,
                 "animated_png": png(animated=True),
                 "progressive_jpeg": jpeg(marker=0xC2),
                 "side_out_of_range": png(200, 400),
                 "pixels_out_of_range": png(256, 300)}
        for code, data in cases.items():
            with self.assertRaises(images.ImageError) as caught:
                images.check(data)
            self.assertEqual(caught.exception.code, code)
        with mock.patch.object(images, "MAX_FILE_BYTES", 10):
            with self.assertRaises(images.ImageError):
                images.check(png())

    def test_an_asset_is_base64_with_a_uuid_reference(self):
        ref, item = images.asset(png())
        self.assertRegex(ref, r"^img-[0-9a-f-]{36}$")
        self.assertEqual(item["format"], "png")
        self.assertEqual(base64.b64decode(item["data_base64"]), png())


def exif_jpeg(orientation: int, order: str = "<") -> bytes:
    """A baseline JPEG whose APP1 Exif carries one Orientation entry."""
    mark = b"II" if order == "<" else b"MM"
    tiff = mark + struct.pack(order + "HI", 42, 8) + struct.pack(order + "H", 1) \
        + struct.pack(order + "HHIH2x", 0x0112, 3, 1, orientation) + struct.pack(order + "I", 0)
    body = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    plain = jpeg()
    return plain[:2] + app1 + plain[2:]


class Rotation(unittest.TestCase):
    def test_a_rotated_photo_is_refused_upright_ones_pass(self):
        for order in ("<", ">"):
            self.assertEqual(images.check(exif_jpeg(1, order)), "jpeg")
            for orientation in (3, 6, 8):
                with self.assertRaises(images.ImageError) as caught:
                    images.check(exif_jpeg(orientation, order))
                self.assertEqual(caught.exception.code, "exif_rotated")

    def test_unreadable_exif_is_read_as_upright(self):
        broken = exif_jpeg(6)
        broken = broken.replace(b"Exif\x00\x00II", b"Exif\x00\x00XX")
        self.assertEqual(images.check(broken), "jpeg")

    def test_other_jpeg_coding_is_refused(self):
        for marker in (0xC3, 0xC9, 0xCA):
            with self.assertRaises(images.ImageError):
                images.check(jpeg(marker=marker))

    def test_corrupt_bytes_raise_only_image_error(self):
        seeds = (png(), jpeg(), exif_jpeg(6), png(animated=True))
        for seed in seeds:
            for cut in range(0, len(seed[:400])):
                for data in (seed[:cut], seed[:cut] + b"\xff" * 8, seed[:cut] + b"\x00" * 8):
                    try:
                        images.check(data)
                    except images.ImageError:
                        pass


class StrictServer(http.server.BaseHTTPRequestHandler):
    """A stand-in for /v1/systemone that enforces the documented image rules before answering."""
    seen: list = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = json.dumps({"state": body["state"], "questions": body["questions"]})
        problems = []
        if self.headers.get(images.DANGER_ZONE_HEADER) != "true":
            problems.append("header")
        for ref, item in body.get("assets", {}).items():
            if ref not in text:
                problems.append("unreferenced")
            raw = base64.b64decode(item["data_base64"], validate=True)
            kind, width, height = images.inspect(raw)
            if not (256 <= width <= 1568 and 256 <= height <= 1568):
                problems.append("size")
        StrictServer.seen.append(body)
        if problems:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": problems}).encode())
            return
        answers = {}
        for name, question in body["questions"].items():
            if question["type"] == "box":
                answers[name] = {"type": "box", "confidence": 0.7,
                                 "box": {"left": 0.1, "top": 0.1, "right": 0.6, "bottom": 0.5}}
            else:
                answers[name] = {"type": "noul", "noul": 0.2}
        reply = json.dumps({"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 1500}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


class OverHttp(unittest.TestCase):
    """The whole request over a real socket, through TYPESAFE_BASE_URL, as TypeSafe would get it."""

    def setUp(self):
        StrictServer.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StrictServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        env = mock.patch.dict(os.environ, {"TYPESAFE_BASE_URL": f"http://127.0.0.1:{self.server.server_port}"})
        env.start()
        self.addCleanup(env.stop)

    def test_a_large_picture_and_a_box_round_trip(self):
        big = png(1568, 1500)
        ref, item = images.asset(big)
        out = client.ask({"screen": ref}, {"visible": client.noul("Is the Submit button visible in `screen`?"),
                                            "where": client.box("the Submit button in `screen`")},
                         assets={ref: item}, timeout=20)
        self.assertEqual(out["answers"]["where"]["box"]["right"], 0.6)
        self.assertEqual(out["input_tokens"], 1500)
        self.assertEqual(base64.b64decode(StrictServer.seen[0]["assets"][ref]["data_base64"]), big)

    def test_two_pictures_in_one_request(self):
        (a, first), (b, second) = images.asset(png()), images.asset(jpeg())
        client.ask({"before": a, "after": b}, {"changed": client.noul("Did `after` change from `before`?")},
                   assets={a: first, b: second}, timeout=20)
        self.assertEqual(set(StrictServer.seen[0]["assets"]), {a, b})


class Requests(unittest.TestCase):
    def setUp(self):
        self.sent = []

    def transport(self, answers):
        def send(body, headers, timeout):
            self.sent.append((json.loads(body), headers))
            return json.dumps({"model": "jev-1.14.0", "answers": answers, "usage": {"input_tokens": 900}}).encode()
        return send

    def test_assets_go_with_the_header(self):
        ref, item = images.asset(png())
        out = client.ask({"tile": ref}, {"ok": client.noul("Is `tile` clipped?")}, assets={ref: item},
                         api_key="k", provider="typesafe", transport=self.transport({"ok": {"type": "noul", "noul": 0.1}}))
        body, headers = self.sent[0]
        self.assertEqual(headers[images.DANGER_ZONE_HEADER], "true")
        self.assertEqual(body["assets"][ref]["format"], "png")
        self.assertEqual(out["answers"]["ok"]["noul"], 0.1)

    def test_text_only_requests_are_unchanged(self):
        client.ask("x", {"ok": client.noul("Is it fine?")}, api_key="k", provider="typesafe",
                   transport=self.transport({"ok": {"type": "noul", "noul": 0.5}}))
        body, headers = self.sent[0]
        self.assertNotIn("assets", body)
        self.assertNotIn(images.DANGER_ZONE_HEADER, headers)

    def test_a_key_is_not_an_image_reference(self):
        ref, item = images.asset(png())
        with self.assertRaises(ValueError):
            client.ask({ref: "only a key"}, {"ok": client.noul("Is it fine?")},
                       assets={ref: item}, api_key="k", provider="typesafe",
                       transport=self.transport({"ok": {"type": "noul", "noul": 0.5}}))
        self.assertEqual(self.sent, [])

    def test_question_type_is_not_an_image_reference(self):
        _, item = images.asset(png())
        with self.assertRaises(ValueError):
            client.ask("no picture", {"ok": client.noul("Is it fine?")}, assets={"noul": item},
                       api_key="k", provider="typesafe",
                       transport=self.transport({"ok": {"type": "noul", "noul": 0.5}}))
        self.assertEqual(self.sent, [])

    def test_unicode_reference_matches_actual_string_values(self):
        _, item = images.asset(png())
        client.ask({"picture": "picture-\u2603"}, {"ok": client.noul("Is it fine?")},
                   assets={"picture-\u2603": item}, api_key="k", provider="typesafe",
                   transport=self.transport({"ok": {"type": "noul", "noul": 0.5}}))
        self.assertEqual(len(self.sent), 1)

    def test_whitespace_is_not_an_asset_reference(self):
        _, item = images.asset(png())
        with self.assertRaises(ValueError):
            client.ask("a b", {"ok": client.noul("Is it fine?")}, assets={" ": item},
                       api_key="k", provider="typesafe",
                       transport=self.transport({"ok": {"type": "noul", "noul": 0.5}}))
        self.assertEqual(self.sent, [])

    def test_an_unreferenced_asset_is_refused_before_sending(self):
        ref, item = images.asset(png())
        with self.assertRaises(ValueError):
            client.ask("no picture here", {"ok": client.noul("Is it fine?")}, assets={ref: item},
                       api_key="k", provider="typesafe", transport=self.transport({}))
        self.assertEqual(self.sent, [])

    def test_images_go_to_typesafe_only(self):
        ref, item = images.asset(png())
        for provider in ("openrouter", "venice", "zen"):
            with self.assertRaises(client.JevError) as caught:
                client.ask({"tile": ref}, {"ok": client.noul("Is `tile` clipped?")}, assets={ref: item},
                           api_key="k", provider=provider, transport=self.transport({}))
            self.assertEqual(caught.exception.code, "images_unsupported")
        self.assertEqual(self.sent, [])

    def test_a_box_is_read_and_checked(self):
        ref, item = images.asset(png())
        good = {"type": "box", "box": {"left": 0.1, "top": 0.2, "right": 0.4, "bottom": 0.5}, "confidence": 0.8}
        out = client.ask({"photo": ref}, {"where": client.box("the mug in `photo`")}, assets={ref: item},
                         api_key="k", provider="typesafe", transport=self.transport({"where": good}))
        self.assertEqual(out["answers"]["where"]["box"]["right"], 0.4)
        self.assertEqual(self.sent[0][1][images.DANGER_ZONE_HEADER], "true")
        flat = dict(good, box={"left": 0.5, "top": 0.2, "right": 0.4, "bottom": 0.5})
        with self.assertRaises(client.JevError) as caught:
            client.ask({"photo": ref}, {"where": client.box("the mug in `photo`")}, assets={ref: item},
                       api_key="k", provider="typesafe", transport=self.transport({"where": flat}))
        self.assertEqual(caught.exception.invariant, "box_has_area")

    def test_a_box_takes_no_criteria(self):
        with self.assertRaises(ValueError):
            client.check_question("where", {"type": "box", "instructions": "the mug", "criteria": {"a": "b"}})


class CloudShadow(Base):
    def setUp(self):
        super().setUp()
        self.image.write_bytes(png())
        self.asked = []

        def fake_ask(state, questions, **kwargs):
            self.asked.append((state, questions, kwargs))
            return {"answers": {"verdict": {"type": "choice", "choice": "No", "confidence": 0.88,
                                            "probabilities": {"Yes": 0.12, "No": 0.88}}},
                    "jev_model": "jev-1.14.0", "input_tokens": 1200}
        patcher = mock.patch.object(self.plugin.cloud_vision.client, "ask", fake_ask)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cloud_rows(self):
        return [row for row in self.rows() if row.get("model") == "typesafe-jev"]

    def test_the_local_shadow_never_sends_a_picture(self):
        self.on()
        self.call()
        self.settle()
        self.assertEqual(self.asked, [])
        self.assertEqual(self.cloud_rows(), [])

    def test_off_by_default_and_the_kill_switch_wins(self):
        self.call()
        self.settle()
        self.plugin.switches.set_mode("vision_cloud", "shadow", shared=True)
        self.plugin.switches.kill_switch("vision_cloud").parent.mkdir(parents=True, exist_ok=True)
        self.plugin.switches.kill_switch("vision_cloud").touch()
        self.call()
        self.settle()
        self.assertEqual(self.asked, [])

    def test_cloud_shadow_asks_and_logs_no_content(self):
        self.plugin.switches.set_mode("vision_cloud", "shadow", shared=True)
        result = self.call()
        self.settle()
        self.assertIsNone(result)
        state, questions, kwargs = self.asked[0]
        self.assertEqual(kwargs["provider"], "typesafe")
        self.assertIn(state["image"], kwargs["assets"])
        self.assertEqual(set(questions["verdict"]["criteria"]), {"Yes", "No"})
        [row] = self.cloud_rows()
        self.assertEqual((row["status"], row["jev_class"], row["agree"]), ("ok", "No", True))
        self.assertEqual(row["input_tokens"], 1200)
        text = json.dumps(row)
        for leak in ("SECRETWORD", str(self.image), QUESTION):
            self.assertNotIn(leak, text)

    def test_unusable_pictures_and_regions_are_skipped(self):
        self.plugin.switches.set_mode("vision_cloud", "shadow", shared=True)
        self.image.write_bytes(png(100, 100))
        self.call()
        self.settle()
        self.assertEqual(self.cloud_rows()[-1]["reason"], "image_side_out_of_range")
        self.image.write_bytes(png())
        self.plugin._on_post_tool_call(
            tool_name="vision_analyze", args={"image_url": str(self.image), "question": QUESTION, "region": [0, 0, 9, 9]},
            session_id="s", status="ok", result="{}", tool_call_id="c", turn_id="t")
        self.settle()
        self.assertEqual(self.cloud_rows()[-1]["reason"], "region_unsupported")
        self.assertEqual(self.asked, [])

    def test_disabling_during_image_preparation_prevents_sending(self):
        self.plugin.switches.set_mode("vision_cloud", "shadow", shared=True)
        asset = self.plugin.cloud_vision.images.asset

        def stop_before_send(path):
            result = asset(path)
            self.plugin.switches.set_mode("vision_cloud", "off", shared=True)
            return result

        with mock.patch.object(self.plugin.cloud_vision.images, "asset", side_effect=stop_before_send):
            self.call()
            self.settle()
        self.assertEqual(self.asked, [])
        self.assertEqual(self.cloud_rows()[-1]["reason"], "disabled_before_send")

    def test_a_jev_error_is_logged_not_raised(self):
        self.plugin.switches.set_mode("vision_cloud", "shadow", shared=True)
        with mock.patch.object(self.plugin.cloud_vision.client, "ask", side_effect=self.plugin.cloud_vision.client.JevError("http_400")):
            self.call()
            self.settle()
        self.assertEqual((self.cloud_rows()[-1]["status"], self.cloud_rows()[-1]["error"]), ("error", "http_400"))


if __name__ == "__main__":
    unittest.main()


def _script(name):
    import importlib.util
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveCheckScript(unittest.TestCase):
    """The live smoke test, run offline: its picture is valid and its scoring is right."""

    def setUp(self):
        self.live = _script("live_images_check")

    def test_the_drawn_picture_is_one_the_api_accepts(self):
        self.assertEqual(images.check(self.live.square_png()), "png")

    def test_overlap(self):
        exact = dict(zip(("left", "top", "right", "bottom"), (v / 512 for v in self.live.SQUARE)))
        self.assertAlmostEqual(self.live.overlap(exact), 1.0)
        self.assertEqual(self.live.overlap({"left": 0.0, "top": 0.6, "right": 0.2, "bottom": 0.9}), 0.0)

    def run_with(self, answers):
        out = {"answers": answers, "jev_model": "jev-1.14.0", "input_tokens": 800, "latency_ms": 400}
        with mock.patch.object(self.live.client, "ask", return_value=out), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as printed:
            code = self.live.main([])
        return code, json.loads(printed.getvalue())

    def test_pass_and_fail(self):
        exact = dict(zip(("left", "top", "right", "bottom"), (v / 512 for v in self.live.SQUARE)))
        good = {"square": {"noul": 0.95}, "circle": {"noul": 0.02},
                "quadrant": {"choice": "top_right", "confidence": 0.9},
                "where": {"box": exact, "confidence": 0.8}}
        code, report = self.run_with(good)
        self.assertEqual((code, report["ok"]), (0, True))
        code, report = self.run_with(dict(good, quadrant={"choice": "bottom_left", "confidence": 0.9}))
        self.assertEqual((code, report["checks"]["quadrant"]["pass"]), (1, False))

    def test_a_refusal_is_reported_not_raised(self):
        with mock.patch.object(self.live.client, "ask", side_effect=self.live.client.JevError("http_400", "header")), \
                mock.patch("sys.stdout", new_callable=__import__("io").StringIO) as printed:
            self.assertEqual(self.live.main(["--no-box"]), 1)
        self.assertEqual(json.loads(printed.getvalue())["error"], "http_400")


class AbScript(unittest.TestCase):
    def test_sensitive_questions_are_skipped_before_reading_or_sending(self):
        ab = _script("vision_cloud_ab")
        with mock.patch.object(ab.images, "asset", return_value=("ref", {})) as materialize, \
                mock.patch.object(ab.client, "ask", return_value={"answers": {"verdict": {"choice": "Yes", "confidence": .9}}}) as ask:
            report = ab.run([{"image": "/not-read.png", "question": "Is the password hunter2 visible?", "label": "Yes"}])
        materialize.assert_not_called()
        ask.assert_not_called()
        self.assertEqual(report["skipped"], {"sensitive_question": 1})

    def test_accuracy_and_cutoffs_hold_no_content(self):
        ab = _script("vision_cloud_ab")
        import tempfile
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tmp = Path(folder.name) / "tile.png"
        tmp.write_bytes(png())
        rows = [{"image": str(tmp), "question": "Is the SECRETWORD logo clipped?", "label": "No"},
                {"image": str(tmp), "question": "Is the logo clipped?", "label": "Yes"},
                {"image": str(tmp), "question": "Describe the image.", "label": "Yes"},
                {"image": "/nonexistent.png", "question": "Is it clipped?", "label": "No"}]
        confidences = iter([0.9, 0.55])

        def fake(state, questions, **kwargs):
            self.assertIn(state["image"], kwargs["assets"])
            return {"answers": {"verdict": {"choice": "No", "confidence": next(confidences)}}, "input_tokens": 100}
        report = ab.run(rows, asker=fake)
        self.assertEqual((report["asked"], report["accuracy"], report["input_tokens"]), (2, 0.5, 200))
        self.assertEqual(report["skipped"], {"shape_or_label": 1, "image_gone": 1})
        at_06 = next(c for c in report["cutoffs"] if c["cutoff"] == 0.6)
        self.assertEqual((at_06["kept"], at_06["accuracy_kept"]), (1, 1.0))
        self.assertNotIn("SECRETWORD", json.dumps(report))
