"""
Minutes in Seconds — Exporters
───────────────────────────────
TXT, DOCX and PDF output from a Transcript object.
"""

from pathlib import Path
from app.pipeline import Transcript, fmt_time


# ─── TXT ──────────────────────────────────────────────────────────────────────

def export_txt(transcript: Transcript, out_path: str):
    lines = [
        f"TRANSCRIPT: {transcript.title}",
        f"Date       : {transcript.date}",
        f"Source     : {Path(transcript.source_file).name}",
        f"Duration   : {fmt_time(transcript.duration)}",
        f"Speakers   : {', '.join(transcript.speakers.values())}",
        "=" * 60, "",
    ]
    for utt in transcript.utterances:
        label = transcript.speakers.get(utt.speaker, utt.speaker)
        lang_tag = f" [{utt.language.upper()}]" if utt.language else ""
        lines += [f"[{fmt_time(utt.start)}] {label}{lang_tag}:", f"  {utt.text}", ""]

    if transcript.summary:
        lines += ["=" * 60, "SUMMARY", "=" * 60, transcript.summary]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─── DOCX ─────────────────────────────────────────────────────────────────────

def export_docx(transcript: Transcript, out_path: str):
    from docx import Document
    from docx.shared import Pt, RGBColor, Inches
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    PALETTE = [
        RGBColor(0, 212, 200),   # teal
        RGBColor(99, 179, 237),  # sky blue
        RGBColor(246, 135, 179), # pink
        RGBColor(252, 176, 64),  # amber
        RGBColor(154, 117, 234), # purple
        RGBColor(104, 211, 145), # green
    ]
    speaker_colors = {
        sid: PALETTE[i % len(PALETTE)]
        for i, sid in enumerate(sorted(transcript.speakers.keys()))
    }

    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1.2)
        section.right_margin = Inches(1.2)

    # Title
    title_p = doc.add_heading(transcript.title, level=1)
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Metadata
    meta_p = doc.add_paragraph()
    meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    mr = meta_p.add_run(f"{transcript.date}   •   Duration: {fmt_time(transcript.duration)}   •   {', '.join(transcript.speakers.values())}")
    mr.font.size = Pt(9)
    mr.font.color.rgb = RGBColor(130, 130, 130)

    doc.add_paragraph()

    # Legend
    leg = doc.add_paragraph()
    leg.add_run("SPEAKERS").bold = True

    for sid, label in transcript.speakers.items():
        lp = doc.add_paragraph(style="List Bullet")
        r = lp.add_run(f"● {label}")
        r.bold = True
        r.font.color.rgb = speaker_colors[sid]

    doc.add_paragraph()

    # Utterances
    for utt in transcript.utterances:
        label = transcript.speakers.get(utt.speaker, utt.speaker)
        color = speaker_colors.get(utt.speaker, RGBColor(0, 0, 0))
        lang_tag = f"  [{utt.language.upper()}]" if utt.language else ""

        header_p = doc.add_paragraph()
        ts = header_p.add_run(f"[{fmt_time(utt.start)}]  ")
        ts.font.size = Pt(8)
        ts.font.color.rgb = RGBColor(160, 160, 160)
        name_r = header_p.add_run(f"{label}{lang_tag}")
        name_r.bold = True
        name_r.font.color.rgb = color
        name_r.font.size = Pt(10)

        speech_p = doc.add_paragraph()
        speech_p.paragraph_format.left_indent = Inches(0.3)
        speech_p.paragraph_format.space_after = Pt(6)
        speech_p.add_run(utt.text).font.size = Pt(11)

    if transcript.summary:
        doc.add_page_break()
        doc.add_heading("Summary", level=2)
        doc.add_paragraph(transcript.summary)

    doc.save(out_path)


# ─── PDF ──────────────────────────────────────────────────────────────────────

def export_pdf(transcript: Transcript, out_path: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.enums import TA_CENTER

    doc = SimpleDocTemplate(out_path, pagesize=A4,
        leftMargin=2.5*cm, rightMargin=2.5*cm,
        topMargin=2.5*cm, bottomMargin=2.5*cm)

    styles = getSampleStyleSheet()

    PALETTE = ["#00d4c8", "#63b3ed", "#f687b3", "#fcb040", "#9a75ea", "#68d391"]
    speaker_styles = {}
    for i, sid in enumerate(sorted(transcript.speakers.keys())):
        speaker_styles[sid] = ParagraphStyle(
            f"Spk_{i}", parent=styles["Normal"],
            fontSize=10, fontName="Helvetica-Bold",
            textColor=colors.HexColor(PALETTE[i % len(PALETTE)]),
            spaceAfter=2,
        )

    title_style = ParagraphStyle("T", parent=styles["Title"],
        fontSize=22, textColor=colors.HexColor("#0a0a1a"),
        spaceAfter=6, alignment=TA_CENTER)
    meta_style = ParagraphStyle("M", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#888888"),
        spaceAfter=16, alignment=TA_CENTER)
    ts_style = ParagraphStyle("TS", parent=styles["Normal"],
        fontSize=8, textColor=colors.HexColor("#aaaaaa"),
        spaceBefore=10, spaceAfter=0)
    speech_style = ParagraphStyle("S", parent=styles["Normal"],
        fontSize=11, textColor=colors.HexColor("#1a1a1a"),
        leftIndent=12, spaceAfter=8, leading=16)

    story = [
        Paragraph(transcript.title, title_style),
        Paragraph(
            f"{transcript.date}  •  Duration: {fmt_time(transcript.duration)}  •  "
            f"{', '.join(transcript.speakers.values())}",
            meta_style
        ),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e0")),
        Spacer(1, 14),
    ]

    for utt in transcript.utterances:
        label = transcript.speakers.get(utt.speaker, utt.speaker)
        lang_tag = f"  [{utt.language.upper()}]" if utt.language else ""
        spk_style = speaker_styles.get(utt.speaker, styles["Normal"])
        safe_text = utt.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        story.append(Paragraph(f"[{fmt_time(utt.start)}]", ts_style))
        story.append(Paragraph(f"{label}{lang_tag}", spk_style))
        story.append(Paragraph(safe_text, speech_style))

    if transcript.summary:
        story += [
            Spacer(1, 20),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e0e0e0")),
            Spacer(1, 8),
            Paragraph("Summary", styles["Heading2"]),
            Paragraph(transcript.summary, styles["Normal"]),
        ]

    doc.build(story)
