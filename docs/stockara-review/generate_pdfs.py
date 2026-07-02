#!/usr/bin/env python3
"""Render Stockara review Markdown documents to PDFs.

The Markdown files in this directory are the editable source of truth. This
script intentionally supports only the Markdown features used by these docs so
future updates remain straightforward.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.platypus import (
    Image,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "pdf"
DOCS = [
    BASE_DIR / "01-stock-data-and-analysis.md",
    BASE_DIR / "02-aws-architecture.md",
    BASE_DIR / "03-web-ui-user-manual.md",
]

INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#586174")
BLUE = colors.HexColor("#2563EB")
GREEN = colors.HexColor("#059669")
AMBER = colors.HexColor("#D97706")
RED = colors.HexColor("#DC2626")
PANEL = colors.HexColor("#F8FAFC")
LINE = colors.HexColor("#CBD5E1")


@dataclass
class RenderState:
    story: list
    styles: dict
    title: str | None = None


def styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "StockaraTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=25,
            leading=31,
            textColor=BLUE,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "subtitle": ParagraphStyle(
            "StockaraSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=22,
        ),
        "h2": ParagraphStyle(
            "StockaraH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            textColor=BLUE,
            spaceBefore=13,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "StockaraH3",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=INK,
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "StockaraBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9.2,
            leading=13,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "StockaraSmall",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "bullet": ParagraphStyle(
            "StockaraBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12.5,
            textColor=INK,
            leftIndent=0,
        ),
        "code": ParagraphStyle(
            "StockaraCode",
            parent=base["Code"],
            fontName="Courier",
            fontSize=7.2,
            leading=9.2,
            textColor=colors.HexColor("#334155"),
            backColor=colors.HexColor("#F1F5F9"),
        ),
    }


def on_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(doc.leftMargin, 1.35 * cm, A4[0] - doc.rightMargin, 1.35 * cm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(doc.leftMargin, 0.85 * cm, "Stockara review documentation")
    canvas.drawRightString(
        A4[0] - doc.rightMargin, 0.85 * cm, f"Page {doc.page}"
    )
    canvas.restoreState()


def markdown_to_pdf(source: Path) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    output = OUTPUT_DIR / f"{source.stem}.pdf"
    state = RenderState(story=[], styles=styles())
    lines = source.read_text(encoding="utf-8").splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        if line.startswith("# "):
            state.title = line[2:].strip()
            state.story.append(Spacer(1, 2.2 * cm))
            state.story.append(Paragraph(escape_inline(state.title), state.styles["title"]))
            idx += 1
            continue
        if line.startswith("Audience:") or line.startswith("Status:"):
            block = []
            while idx < len(lines) and (
                lines[idx].startswith("Audience:") or lines[idx].startswith("Status:")
            ):
                block.append(escape_inline(lines[idx]))
                idx += 1
            state.story.append(Paragraph("<br/>".join(block), state.styles["subtitle"]))
            continue
        if line.startswith("## "):
            state.story.append(Paragraph(escape_inline(line[3:].strip()), state.styles["h2"]))
            idx += 1
            continue
        if line.startswith("### "):
            state.story.append(Paragraph(escape_inline(line[4:].strip()), state.styles["h3"]))
            idx += 1
            continue
        if line.startswith("```"):
            lang = line[3:].strip()
            block = []
            idx += 1
            while idx < len(lines) and not lines[idx].startswith("```"):
                block.append(lines[idx])
                idx += 1
            idx += 1
            if lang == "mermaid":
                state.story.append(flowchart_image("\n".join(block)))
                state.story.append(Spacer(1, 8))
            else:
                state.story.append(Preformatted("\n".join(block), state.styles["code"]))
                state.story.append(Spacer(1, 6))
            continue
        if line.startswith("|") and idx + 1 < len(lines) and lines[idx + 1].startswith("|"):
            table_lines = []
            while idx < len(lines) and lines[idx].startswith("|"):
                table_lines.append(lines[idx])
                idx += 1
            state.story.append(markdown_table(table_lines, state.styles))
            state.story.append(Spacer(1, 7))
            continue
        if line.startswith("- "):
            items = []
            while idx < len(lines) and lines[idx].startswith("- "):
                items.append(
                    ListItem(
                        Paragraph(escape_inline(lines[idx][2:].strip()), state.styles["bullet"]),
                        leftIndent=12,
                    )
                )
                idx += 1
            state.story.append(
                ListFlowable(
                    items,
                    bulletType="bullet",
                    start="circle",
                    leftIndent=15,
                    bulletFontSize=6,
                    bulletOffsetY=1,
                )
            )
            state.story.append(Spacer(1, 4))
            continue
        paragraph = [line.strip()]
        idx += 1
        while idx < len(lines) and lines[idx].strip() and not starts_block(lines[idx]):
            paragraph.append(lines[idx].strip())
            idx += 1
        state.story.append(
            Paragraph(escape_inline(" ".join(paragraph)), state.styles["body"])
        )

    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=1.65 * cm,
        rightMargin=1.65 * cm,
        topMargin=1.65 * cm,
        bottomMargin=1.75 * cm,
        title=state.title or source.stem,
        author="Stockara",
    )
    doc.build(state.story, onFirstPage=on_page, onLaterPages=on_page)
    return output


def starts_block(line: str) -> bool:
    return (
        line.startswith("#")
        or line.startswith("- ")
        or line.startswith("|")
        or line.startswith("```")
        or line.startswith("Audience:")
        or line.startswith("Status:")
    )


def escape_inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return text


def markdown_table(lines: list[str], style_map: dict) -> Table:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append([Paragraph(escape_inline(cell), style_map["small"]) for cell in cells])
    col_count = max(len(row) for row in rows)
    for row in rows:
        while len(row) < col_count:
            row.append(Paragraph("", style_map["small"]))
    width = A4[0] - 3.3 * cm
    table = Table(rows, colWidths=[width / col_count] * col_count, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF2FF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.35, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def flowchart_image(source: str) -> Image:
    return flowchart_drawing(source)


def flowchart_drawing(source: str) -> Drawing:
    labels = extract_mermaid_labels(source)
    if not labels:
        labels = ["Start", "Process", "Output"]
    labels = labels[:18]
    cols = 4 if len(labels) > 10 else 3
    rows = (len(labels) + cols - 1) // cols
    width = 16.4 * cm
    height = min(10.5 * cm, max(3.0 * cm, rows * 1.65 * cm))
    drawing = Drawing(width, height)
    palette = ["#DBEAFE", "#D1FAE5", "#FEF3C7", "#FEE2E2", "#E0E7FF"]
    edge = ["#2563EB", "#059669", "#D97706", "#DC2626", "#4F46E5"]
    box_w = width / cols * 0.72
    box_h = min(0.95 * cm, height / rows * 0.68)
    x_gap = width / cols
    y_gap = height / rows
    positions = []
    for i, label in enumerate(labels):
        row = rows - 1 - i // cols
        col = i % cols
        x = col * x_gap + x_gap / 2
        y = row * y_gap + y_gap / 2
        positions.append((x, y))
        drawing.add(
            Rect(
                x - box_w / 2,
                y - box_h / 2,
                box_w,
                box_h,
                rx=5,
                ry=5,
                fillColor=colors.HexColor(palette[i % len(palette)]),
                strokeColor=colors.HexColor(edge[i % len(edge)]),
                strokeWidth=1,
            )
        )
        wrapped = wrap_label(label).splitlines()
        start_y = y + (len(wrapped) - 1) * 4
        for line_index, text in enumerate(wrapped):
            drawing.add(
                String(
                    x,
                    start_y - line_index * 8,
                    text,
                    textAnchor="middle",
                    fontName="Helvetica",
                    fontSize=6.8,
                    fillColor=INK,
                )
            )

    for i in range(len(positions) - 1):
        x1, y1 = positions[i]
        x2, y2 = positions[i + 1]
        if abs(y1 - y2) >= 1:
            continue
        start_x = x1 + box_w / 2 + 3
        end_x = x2 - box_w / 2 - 3
        start_y = end_y = y1
        drawing.add(
            Line(
                start_x,
                start_y,
                end_x,
                end_y,
                strokeColor=colors.HexColor("#64748B"),
                strokeWidth=0.8,
            )
        )
        add_arrow_head(drawing, start_x, start_y, end_x, end_y)

    return drawing


def add_arrow_head(
    drawing: Drawing, start_x: float, start_y: float, end_x: float, end_y: float
) -> None:
    dx = end_x - start_x
    dy = end_y - start_y
    if abs(dx) >= abs(dy):
        if dx >= 0:
            points = [end_x, end_y, end_x - 5, end_y + 3, end_x - 5, end_y - 3]
        else:
            points = [end_x, end_y, end_x + 5, end_y + 3, end_x + 5, end_y - 3]
    else:
        if dy >= 0:
            points = [end_x, end_y, end_x - 3, end_y - 5, end_x + 3, end_y - 5]
        else:
            points = [end_x, end_y, end_x - 3, end_y + 5, end_x + 3, end_y + 5]
    drawing.add(
        Polygon(
            points,
            fillColor=colors.HexColor("#64748B"),
            strokeColor=colors.HexColor("#64748B"),
        )
    )


def extract_mermaid_labels(source: str) -> list[str]:
    labels: list[str] = []
    seen = set()
    for quoted in re.findall(r'\["([^"]+)"\]', source):
        if quoted not in seen:
            labels.append(quoted)
            seen.add(quoted)
    for quoted in re.findall(r'\|([^|]+)\|', source):
        label = quoted.strip()
        if label.lower() in {"yes", "no"}:
            continue
        if label and label not in seen and len(label) < 35:
            labels.append(label)
            seen.add(label)
    return labels


def wrap_label(label: str) -> str:
    words = label.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(w) for w in current) + len(word) + len(current) > 18:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines[:4])


def main() -> None:
    outputs = [markdown_to_pdf(source) for source in DOCS]
    print("Generated PDFs:")
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
