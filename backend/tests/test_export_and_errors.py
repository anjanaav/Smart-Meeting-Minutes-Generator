"""Export (text, PDF, download header), HTTP error pass-through, and configuration guards."""
import io
import re
import time
import unittest
from urllib.parse import unquote

from app.config import Config
from app.export import PDF_SCRIPT_NOTE, pdf_safe, render_pdf, render_text
from app.routes import content_disposition

from .helpers import AppCase, audio, seg

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None


def sample_doc(**over):
    doc = {"meeting_title": "Release sync", "meeting_date": "2026-10-19T10:00:00+00:00", "duration_seconds": 125,
           "participants": [{"name": "Alice"}, {"name": "Bob"}], "summary": "The team agreed the release plan.",
           "key_points": [{"topic": "Release", "details": "Ship after testing.", "segment_ids": [1]}],
           "decisions": [{"description": "Ship on the 15th", "speaker": "Alice", "context": "", "evidence": "we ship", "segment_ids": [1]}],
           "action_items": [{"task": "Send notes", "assignee": None, "deadline": None, "status": "pending", "evidence": "e", "segment_ids": [2]}],
           "transcript": [{"id": 1, "speaker": "Alice", "text": "We ship on the fifteenth.", "start_time": 3, "end_time": 6, "uncertain": False},
                          {"id": 2, "speaker": "Bob", "text": "I will send the notes.", "start_time": 7, "end_time": 9, "uncertain": True}],
           "warnings": []}
    doc.update(over)
    return doc


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@unittest.skipIf(PdfReader is None, "pypdf is not installed")
class PdfContentTests(unittest.TestCase):
    def test_pdf_contains_every_section_and_the_transcript_with_timestamps(self):
        text = flat(pdf_text(render_pdf(sample_doc())))
        for needle in ("Release sync", "Meeting Summary", "Key Discussion Points", "Decisions Made", "Action Items", "Full Transcript",
                       "Ship on the 15th", "Assignee: unassigned", "Deadline: unspecified", "[00:00:03] Alice: We ship on the fifteenth.",
                       "(unclear)", "Participants: Alice, Bob"):
            self.assertIn(needle, text)

    def test_markup_characters_appear_literally_and_do_not_break_layout(self):
        text = flat(pdf_text(render_pdf(sample_doc(meeting_title="R&D <sync> & Q&A", summary="Use <b>bold</b> & more"))))
        self.assertIn("R&D <sync> & Q&A", text)
        self.assertIn("Use <b>bold</b> & more", text)

    def test_latin_and_cyrillic_are_preserved_and_no_note_is_added(self):
        text = flat(pdf_text(render_pdf(sample_doc(summary="Café résumé naïve Zürich ñandú. Привет мир."))))
        self.assertIn("Café résumé naïve Zürich ñandú.", text)
        self.assertIn("Привет мир.", text)
        self.assertNotIn("cannot display", text)

    def test_unsupported_scripts_are_marked_not_silently_dropped(self):
        text = flat(pdf_text(render_pdf(sample_doc(summary="Start. മലയാളം പരീക്ഷണം. End."))))
        self.assertIn("?", text)
        self.assertIn("cannot display correctly", text)           # the reader is told, and pointed to the .txt export
        self.assertIn("Start.", text)
        self.assertIn("End.", text)

    def test_the_text_export_always_keeps_the_full_unicode_text(self):
        out = render_text(sample_doc(summary="മലയാളം പരീക്ഷണം हिंदी"))
        self.assertIn("മലയാളം പരീക്ഷണം हिंदी", out)

    def test_long_transcript_spans_pages_and_loses_nothing(self):
        lines = [{"id": i, "speaker": "Alice", "text": f"Statement number {i} about the plan.", "start_time": i, "end_time": i + 1, "uncertain": False}
                 for i in range(1, 2001)]
        started = time.time()
        data = render_pdf(sample_doc(transcript=lines))
        self.assertLess(time.time() - started, 20)
        reader = PdfReader(io.BytesIO(data))
        self.assertGreater(len(reader.pages), 5)
        text = flat(pdf_text(data))
        for n in (1, 777, 2000):
            self.assertIn(f"Statement number {n} about the plan.", text)

    def test_one_extremely_long_word_does_not_crash(self):
        data = render_pdf(sample_doc(summary="x" * 400))
        self.assertTrue(data.startswith(b"%PDF"))

    def test_empty_sections_say_so(self):
        text = flat(pdf_text(render_pdf(sample_doc(key_points=[], decisions=[], action_items=[], transcript=[], participants=[]))))
        self.assertGreaterEqual(text.count("None recorded."), 4)

    def test_without_a_unicode_font_latin1_survives_and_other_scripts_are_flagged(self):
        from unittest import mock
        with mock.patch("app.export._register_font", return_value="Helvetica"):
            text = flat(pdf_text(render_pdf(sample_doc(summary="Café Привет"))))
        self.assertIn("Café", text)
        self.assertNotIn("Привет", text)
        self.assertIn("cannot display correctly", text)


class PdfSafeTests(unittest.TestCase):
    def test_a_run_of_unsupported_characters_becomes_one_marker(self):
        self.assertEqual(pdf_safe("ab XYZ cd", lambda ch: ch not in "XYZ"), ("ab ? cd", 1))

    def test_separate_runs_are_counted_separately_and_newlines_survive(self):
        self.assertEqual(pdf_safe("a1\nb22c", lambda ch: not ch.isdigit()), ("a?\nb?c", 2))

    def test_nothing_to_replace(self):
        self.assertEqual(pdf_safe("plain text", lambda ch: True), ("plain text", 0))


class DownloadHeaderTests(unittest.TestCase):
    def check(self, title, ext="pdf"):
        header = content_disposition(title, ext)
        header.encode("latin-1")                                   # a WSGI server needs this to hold
        self.assertNotIn("\n", header)
        return header

    def test_ascii_title(self):
        self.assertEqual(self.check("Release sync"), "attachment; filename=\"Release_sync_minutes.pdf\"; filename*=UTF-8''Release%20sync_minutes.pdf")

    def test_malayalam_title_is_header_safe_and_keeps_its_real_name(self):
        header = self.check("മലയാളം യോഗം")
        self.assertIn('filename="meeting_minutes.pdf"', header)   # ASCII fallback
        self.assertEqual(unquote(header.split("filename*=UTF-8''")[1]), "മലയാളം യോഗം_minutes.pdf")

    def test_mixed_title_keeps_its_ascii_part_in_the_fallback(self):
        self.assertIn('filename="Q3_review_minutes.pdf"', self.check("മലയാളം Q3 review"))

    def test_quotes_slashes_control_characters_and_traversal_are_neutralised(self):
        header = self.check('He said "hi"/..\\evil\r\nSet-Cookie: x=1')
        self.assertNotIn("Set-Cookie: x", header.split("filename*")[0])
        self.assertEqual(header.count('"'), 2)                     # only the quotes around the fallback name
        self.assertNotIn("/", header.split("filename*=UTF-8''")[1].replace("%2F", ""))

    def test_empty_and_dots_only_titles_fall_back(self):
        for title in ("", "   ", "...", None):
            self.assertIn('filename="meeting_minutes.txt"', self.check(title, "txt"))

    def test_very_long_title_is_cut(self):
        self.assertLess(len(self.check("a" * 500)), 300)


class ExportEndpointTests(AppCase):
    def complete_meeting(self, title):
        mid, token = self.start()
        self.store.mutate_meeting(mid, lambda m: {"title": title})
        self.upload(mid, token, audio(seg(0, 4, "We will ship version two on the fifteenth."), seg(4, 8, "I will send the release notes by Friday.")))
        self.complete(mid, token)
        self.assertEqual(self.status(mid)["status"], "completed")
        return mid

    def test_export_works_for_a_malayalam_title_and_the_header_is_sendable(self):
        mid = self.complete_meeting("മലയാളം യോഗം / Q3 review")
        for fmt, mime in (("txt", "text/plain"), ("pdf", "application/pdf")):
            r = self.client.get(f"/api/meetings/{mid}/export?format={fmt}", headers=self.hdr())
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.mimetype.startswith(mime))
            environ = {"REQUEST_METHOD": "GET", "wsgi.url_scheme": "http", "SERVER_NAME": "x", "SERVER_PORT": "80", "PATH_INFO": "/", "HTTP_HOST": "x"}
            for _k, value in r.get_wsgi_headers(environ):
                value.encode("latin-1")                            # what waitress/gunicorn require
            self.assertIn("filename*=UTF-8''", r.headers["Content-Disposition"])

    def test_unknown_format_and_unfinished_meeting_are_clear_errors(self):
        mid, _ = self.start()
        self.assertEqual(self.client.get(f"/api/meetings/{mid}/export?format=docx", headers=self.hdr()).get_json()["error"]["code"], "UNSUPPORTED_FORMAT")
        r = self.client.get(f"/api/meetings/{mid}/export?format=pdf", headers=self.hdr())
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (409, "MINUTES_NOT_READY"))


class HttpErrorTests(AppCase):
    def add_aborting_routes(self):
        from flask import abort
        self.app.add_url_rule("/api/_t400", "t400", lambda: abort(400))
        self.app.add_url_rule("/api/_t408", "t408", lambda: abort(408))
        self.app.add_url_rule("/api/_t415", "t415", lambda: abort(415))

    def test_werkzeug_http_errors_keep_their_status_and_use_the_error_envelope(self):
        self.add_aborting_routes()
        for path, status, code in (("/api/_t400", 400, "BAD_REQUEST"), ("/api/_t408", 408, "REQUEST_TIMEOUT"), ("/api/_t415", 415, "UNSUPPORTED_MEDIA_TYPE")):
            r = self.client.get(path)
            self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (status, code), path)
            self.assertTrue(r.get_json()["error"]["message"])

    def test_a_broken_upload_is_a_client_error_not_a_server_error(self):
        mid, token = self.start()
        r = self.client.post(f"/api/bot/meetings/{mid}/audio", data=b"--nope\r\ngarbage", headers=self.bot_h(token),
                             content_type="multipart/form-data; boundary=missing")
        self.assertEqual(r.status_code, 400)

    def test_non_json_body_is_the_apis_own_400(self):
        r = self.client.post("/api/meetings", data="not json", headers={**self.hdr(), "Content-Type": "text/plain"})
        self.assertEqual((r.status_code, r.get_json()["error"]["code"]), (400, "INVALID_JSON"))

    def test_unknown_route_and_wrong_method(self):
        self.assertEqual(self.client.get("/api/nothing-here").get_json()["error"]["code"], "NOT_FOUND")
        self.assertEqual(self.client.delete("/api/health").status_code, 405)

    def test_genuine_bugs_still_return_a_500_without_details(self):
        self.app.extensions["smmg"].store.list_meetings = lambda *a, **k: 1 / 0
        with self.assertLogs("app", level="ERROR"):                # the traceback goes to the log, not to the client
            r = self.client.get("/api/meetings", headers=self.hdr())
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("ZeroDivision", r.get_data(as_text=True))


class ConfigGuardTests(unittest.TestCase):
    def test_dev_auth_and_memory_store_are_refused_in_production(self):
        with self.assertRaises(ValueError):
            Config(env="production", auth_mode="dev", store_backend="firestore").validate()
        with self.assertRaises(ValueError):
            Config(env="production", auth_mode="firebase", store_backend="memory").validate()
        Config(env="production", auth_mode="firebase", store_backend="firestore").validate()

    def test_unknown_modes_are_refused(self):
        with self.assertRaises(ValueError):
            Config(auth_mode="magic").validate()
        with self.assertRaises(ValueError):
            Config(store_backend="sqlite").validate()


if __name__ == "__main__":
    unittest.main()
