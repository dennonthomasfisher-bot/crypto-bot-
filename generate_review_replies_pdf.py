#!/usr/bin/env python3
"""Generate a professional one-page PDF: Sample Review Replies – Baz Barbershop (Irvine)"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

WIDTH, HEIGHT = A4
OUTPUT = "Sample_Review_Replies_Baz_Barbershop.pdf"

# Colors
BLACK = HexColor("#1a1a1a")
DARK_GREY = HexColor("#333333")
MID_GREY = HexColor("#555555")
LIGHT_GREY = HexColor("#888888")
ACCENT = HexColor("#2c2c2c")
BG_LIGHT = HexColor("#f5f5f5")
RULE_COLOR = HexColor("#cccccc")


def draw_wrapped_text(c, text, x, y, font, size, color, max_width, leading=None):
    """Draw text that wraps within max_width. Returns the y position after the text."""
    if leading is None:
        leading = size * 1.35
    c.setFont(font, size)
    c.setFillColor(color)

    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test = f"{current_line} {word}".strip()
        if c.stringWidth(test, font, size) <= max_width:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_pdf():
    c = canvas.Canvas(OUTPUT, pagesize=A4)
    c.setTitle("Sample Review Replies - Baz Barbershop (Irvine)")

    margin_left = 40 * mm
    margin_right = 40 * mm
    content_width = WIDTH - margin_left - margin_right
    y = HEIGHT - 35 * mm

    # === HEADER ===
    # Brand name
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(BLACK)
    c.drawCentredString(WIDTH / 2, y, "ReviewPilot")
    y -= 7 * mm

    # Subtitle
    c.setFont("Helvetica", 9)
    c.setFillColor(LIGHT_GREY)
    c.drawCentredString(WIDTH / 2, y, "Helping local barbers stay on top of customer reviews")
    y -= 5 * mm

    # Thin rule
    c.setStrokeColor(RULE_COLOR)
    c.setLineWidth(0.5)
    c.line(margin_left, y, WIDTH - margin_right, y)
    y -= 10 * mm

    # === Document Title ===
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(BLACK)
    c.drawCentredString(WIDTH / 2, y, "Sample Review Replies \u2013 Baz Barbershop (Irvine)")
    y -= 9 * mm

    # Section title
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(DARK_GREY)
    c.drawString(margin_left, y, "Example Customer Reviews & Replies")
    y -= 3 * mm

    # Thin rule
    c.setStrokeColor(RULE_COLOR)
    c.line(margin_left, y, WIDTH - margin_right, y)
    y -= 7 * mm

    # === REVIEWS ===
    reviews = [
        (
            "\u201cGreat experience, felt looked after the whole time\u201d",
            "\u201cThank you so much, Joshua \u2014 that genuinely means a lot to us! We always want every "
            "client to feel looked after from start to finish, and it\u2019s great to know the little extras "
            "made a difference. Can\u2019t wait to see you back in the chair!\u201d",
        ),
        (
            "\u201cAmazing before and after transformation\u201d",
            "\u201cCheers Louis, love seeing the before and after \u2014 that\u2019s a serious transformation! "
            "Really glad you\u2019re happy with the hair, beard and shave. Come back and see us any time!\u201d",
        ),
        (
            "\u201cFirst visit and I\u2019ll definitely be back\u201d",
            "\u201cWelcome to the Baz family! So glad your first visit left you feeling like a new person "
            "\u2014 that\u2019s exactly the vibe we go for. See you for the next one!\u201d",
        ),
        (
            "\u201cGreat atmosphere and banter\u201d",
            "\u201cHa, love it James \u2014 the banter\u2019s always free! Really glad Baz looked after you, "
            "come back and see us any time.\u201d",
        ),
        (
            "\u201cBest haircut I\u2019ve had in years\u201d",
            "\u201cThat\u2019s brilliant to hear, Graeme \u2014 the eyebrows and ear work are all part of the "
            "full service here! Really glad it was your best cut in a couple of years, that\u2019s exactly "
            "what we aim for. See you again soon!\u201d",
        ),
    ]

    for i, (review, reply) in enumerate(reviews):
        # Review label + text
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MID_GREY)
        c.drawString(margin_left, y, f"Customer Review:")
        y -= 4 * mm

        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColor(DARK_GREY)
        y = draw_wrapped_text(c, review, margin_left + 3 * mm, y, "Helvetica-Oblique", 8.5, DARK_GREY, content_width - 3 * mm, leading=11)
        y -= 1.5 * mm

        # Reply label + text
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(MID_GREY)
        c.drawString(margin_left, y, "Reply:")
        y -= 4 * mm

        y = draw_wrapped_text(c, reply, margin_left + 3 * mm, y, "Helvetica", 8.5, DARK_GREY, content_width - 3 * mm, leading=11)

        # Separator between reviews (not after last)
        if i < len(reviews) - 1:
            y -= 2 * mm
            c.setStrokeColor(HexColor("#e0e0e0"))
            c.setLineWidth(0.3)
            c.line(margin_left + 10 * mm, y, WIDTH - margin_right - 10 * mm, y)
            y -= 5 * mm

    # === WHAT THIS HELPS WITH ===
    y -= 5 * mm
    c.setStrokeColor(RULE_COLOR)
    c.setLineWidth(0.5)
    c.line(margin_left, y, WIDTH - margin_right, y)
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColor(BLACK)
    c.drawString(margin_left, y, "What this helps with:")
    y -= 5.5 * mm

    benefits = [
        "All new reviews replied to within 24 hours",
        "Happier, returning customers",
        "Stronger online reputation",
        "Saves you time",
        "Simple and hassle-free",
    ]

    c.setFont("Helvetica", 8.5)
    c.setFillColor(MID_GREY)
    for benefit in benefits:
        c.drawString(margin_left + 4 * mm, y, f"\u2022   {benefit}")
        y -= 4.2 * mm

    # === FOOTER ===
    y -= 4 * mm
    c.setStrokeColor(RULE_COLOR)
    c.setLineWidth(0.5)
    c.line(margin_left, y, WIDTH - margin_right, y)
    y -= 5.5 * mm

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(LIGHT_GREY)
    c.drawCentredString(WIDTH / 2, y, "Several local barbers in North Ayrshire are already using ReviewPilot")

    c.save()
    print(f"PDF created: {OUTPUT}")


if __name__ == "__main__":
    build_pdf()
