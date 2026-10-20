"""Export of generated minutes as plain text or PDF."""
from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape

from .generator import fmt_ts


def _header(doc: dict) -> list:
    lines = [("title", doc["meeting_title"]), ("meta", f'Date: {doc["meeting_date"]}')]
    if doc.get("duration_seconds"):
        lines.append(("meta", f'Duration: {fmt_ts(doc["duration_seconds"])}'))
    if doc["participants"]:
        lines.append(("meta", "Participants: " + ", ".join(p["name"] for p in doc["participants"])))
    return lines


def _sections(doc: dict) -> list:
    """[(heading, [lines])] in the order shown in the app."""
    def or_none(lines):
        return lines or ["None recorded."]

    summary = [doc["summary"] or "No summary available."]
    key_points = or_none([f'{k["topic"]}: {k["details"]}' for k in doc["key_points"]])
    decisions = or_none([d["description"] + (f' ({d["speaker"]})' if d["speaker"] else "") for d in doc["decisions"]])
    actions = or_none([
        f'{a["task"]}  [Assignee: {a["assignee"] or "unassigned"}; Deadline: {a["deadline"] or "unspecified"}; Status: {a["status"]}]'
        for a in doc["action_items"]
    ])
    transcript = or_none([f'[{fmt_ts(t["start_time"])}] {t["speaker"]}: {t["text"]}' + (" (unclear)" if t.get("uncertain") else "") for t in doc["transcript"]])
    sections = [("Meeting Summary", summary), ("Key Discussion Points", key_points), ("Decisions Made", decisions),
                ("Action Items", actions), ("Full Transcript", transcript)]
    if doc.get("warnings"):
        sections.insert(0, ("Notes", list(doc["warnings"])))
    return sections


def render_text(doc: dict) -> str:
    out = []
    for kind, text in _header(doc):
        out.append(text.upper() if kind == "title" else text)
    out.append("=" * 40)
    for heading, lines in _sections(doc):
        out += ["", heading, "-" * len(heading)] + [f"- {l}" if heading in ("Key Discussion Points", "Decisions Made", "Action Items") else l for l in lines]
    return "\n".join(out) + "\n"


def _register_font():
    """Use a Unicode TTF if the system has one; the built-in Helvetica only covers Latin text."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf",
                 "C:/Windows/Fonts/arial.ttf", "/Library/Fonts/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"):
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont("SMMGBody", path))
                return "SMMGBody"
            except Exception:
                continue
    return "Helvetica"


PDF_SCRIPT_NOTE = ("Some text in this meeting uses a script that this PDF cannot display correctly (for example Hindi or Malayalam). "
                   "Those parts are shown as '?'. The text export (.txt) contains the complete text.")


def _glyph_check(font: str):
    """Return a function telling whether ``font`` can draw a character (anything else would silently disappear)."""
    from reportlab.pdfbase import pdfmetrics

    if font == "Helvetica":
        def latin1_ok(ch: str) -> bool:
            try:
                ch.encode("cp1252")
                return True
            except UnicodeEncodeError:
                return False
        return latin1_ok
    cmap = pdfmetrics.getFont(font).face.charToGlyph
    return lambda ch: ord(ch) in cmap


def pdf_safe(text: str, can_draw) -> tuple:
    """Replace each run of characters the font cannot draw by one '?', so nothing vanishes without a trace.

    Returns (text, replaced_run_count)."""
    out, runs, in_run = [], 0, False
    for ch in text:
        if ch in "\n\r\t" or can_draw(ch):
            out.append(ch)
            in_run = False
        else:
            if not in_run:
                out.append("?")
                runs += 1
            in_run = True
    return "".join(out), runs


def render_pdf(doc: dict) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    font = _register_font()
    base = getSampleStyleSheet()
    title = ParagraphStyle("t", parent=base["Title"], fontName=font, fontSize=18)
    h = ParagraphStyle("h", parent=base["Heading2"], fontName=font, spaceBefore=10)
    body = ParagraphStyle("b", parent=base["BodyText"], fontName=font, fontSize=10, leading=14)
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=doc["meeting_title"])
    can_draw, replaced, story = _glyph_check(font), 0, []

    def safe(text: str) -> str:
        nonlocal replaced
        clean, n = pdf_safe(text, can_draw)
        replaced += n
        return escape(clean)

    header = [(kind, safe(text)) for kind, text in _header(doc)]
    sections = [(safe(heading), [safe(line) for line in lines]) for heading, lines in _sections(doc)]
    for kind, text in header:
        story.append(Paragraph(text, title if kind == "title" else body))
    if replaced:
        story.append(Spacer(1, 4))
        story.append(Paragraph(escape(PDF_SCRIPT_NOTE), body))
    for heading, lines in sections:
        story.append(Paragraph(heading, h))
        for line in lines:
            story.append(Paragraph(line, body))
            story.append(Spacer(1, 3))
    pdf.build(story)
    return buf.getvalue()
