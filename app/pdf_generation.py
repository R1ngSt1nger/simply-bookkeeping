import os
import io
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth

from .files import business_logo_path
from .themes import get_theme


def _wrap_text(text, font_name, font_size, max_width):
    """Word-wrap plain text to fit within max_width, returning a list of lines."""
    words = text.split()
    if not words:
        return [""]
    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_right_label_value(c, right_x, y, label, value, font_size):
    """Draws 'label value' ending exactly at right_x, with only the label in
    bold — used for the header meta lines like 'Invoice #: INV-0001'."""
    c.setFont("Helvetica", font_size)
    c.drawRightString(right_x, y, value)
    value_width = stringWidth(value, "Helvetica", font_size)
    c.setFont("Helvetica-Bold", font_size)
    c.drawRightString(right_x - value_width, y, label)


def _draw_left_label_value(c, left_x, y, label, value, font_size):
    """Draws 'label value' starting at left_x, with only the label in bold —
    used for lines like 'Reference: QUO-0005'."""
    c.setFont("Helvetica-Bold", font_size)
    c.drawString(left_x, y, label)
    label_width = stringWidth(label, "Helvetica-Bold", font_size)
    c.setFont("Helvetica", font_size)
    c.drawString(left_x + label_width, y, value)


def _draw_header(c, biz, theme_key, left, right, y, heading_text):
    """Logo (top-left), business details to its right, document heading top-right.
    Returns the y position to continue drawing from below the header."""
    theme = get_theme(theme_key)
    ink = colors.HexColor(theme["ink"])
    grey = colors.HexColor("#8A8D85")

    logo_width = 22 * mm
    logo_height = 16 * mm
    logo_bottom = y
    text_x = left

    if biz.logo_filename:
        logo_path = business_logo_path(biz.slug, biz.logo_filename)
        if os.path.exists(logo_path):
            try:
                c.drawImage(logo_path, left, y - logo_height, width=logo_width, height=logo_height,
                             preserveAspectRatio=True, mask="auto")
                logo_bottom = y - logo_height
                text_x = left + logo_width + 6 * mm
            except Exception:
                pass

    name_y = y
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(ink)
    c.drawString(text_x, name_y, biz.name)
    name_y -= 6 * mm

    c.setFont("Helvetica", 9)
    c.setFillColor(grey)
    detail_lines = []
    if biz.abn:
        detail_lines.append(f"ABN {biz.abn}")
    addr_bits = [biz.address_line, " ".join(filter(None, [biz.suburb, biz.state, biz.postcode]))]
    addr_bits = [b for b in addr_bits if b]
    detail_lines.extend(addr_bits)
    contact_bits = [b for b in [biz.phone, biz.email] if b]
    if contact_bits:
        detail_lines.append(" · ".join(contact_bits))
    for line in detail_lines:
        c.drawString(text_x, name_y, line)
        name_y -= 4.5 * mm

    meta_y = y
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(ink)
    c.drawRightString(right, meta_y, heading_text)
    meta_y -= 8 * mm

    return name_y, logo_bottom, meta_y


def generate_invoice_pdf(tx, biz, theme_key) -> bytes:
    theme = get_theme(theme_key)
    ink = colors.HexColor(theme["ink"])
    rust = colors.HexColor("#D1493C")
    green = colors.HexColor("#1E9E6B")
    grey = colors.HexColor("#8A8D85")
    line_col = colors.HexColor(theme["line"])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 20 * mm
    right = width - 20 * mm
    y = height - 20 * mm

    name_y, logo_bottom, meta_y = _draw_header(c, biz, theme_key, left, right, y, "INVOICE")
    c.setFillColor(ink)
    _draw_right_label_value(c, right, meta_y, "Invoice #: ", f"{tx.invoice_number or '—'}", 9.5)
    meta_y -= 5 * mm
    _draw_right_label_value(c, right, meta_y, "Issue date: ", tx.date.strftime('%d %b %Y'), 9.5)
    meta_y -= 5 * mm
    if tx.invoice_due_date:
        _draw_right_label_value(c, right, meta_y, "Due date: ", tx.invoice_due_date.strftime('%d %b %Y'), 9.5)
        meta_y -= 5 * mm

    y = min(name_y, logo_bottom, meta_y) - 8 * mm

    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 8 * mm

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "BILL TO")
    y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    if tx.contact:
        c.drawString(left, y, tx.contact.display_name)
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        if tx.contact.email:
            c.drawString(left, y, tx.contact.email)
            y -= 4.5 * mm
        if tx.contact.phone:
            c.drawString(left, y, tx.contact.phone)
            y -= 4.5 * mm
    if tx.reference:
        c.setFillColor(grey)
        _draw_left_label_value(c, left, y, "Reference: ", tx.reference, 9)
        y -= 4.5 * mm

    y -= 6 * mm

    amount_col_width = 28 * mm
    desc_max_width = (right - amount_col_width) - left

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "DESCRIPTION")
    c.drawRightString(right, y, "AMOUNT")
    y -= 3 * mm
    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    c.setFillColor(ink)
    row_shade = colors.HexColor("#F3F2EE")
    for i, li in enumerate(tx.line_items):
        desc_lines = _wrap_text(li.description, "Helvetica", 10, desc_max_width)
        row_height = (4.5 * mm) * (len(desc_lines) - 1) + 6 * mm

        if i % 2 == 1:
            c.setFillColor(row_shade)
            c.rect(left - 3 * mm, y - row_height + 4.5 * mm, (right - left) + 6 * mm, row_height, fill=1, stroke=0)
            c.setFillColor(ink)

        amt = float(li.amount)
        c.drawString(left, y, desc_lines[0])
        c.drawRightString(right, y, f"{'-' if amt < 0 else ''}${abs(amt):,.2f}")
        for extra_line in desc_lines[1:]:
            y -= 4.5 * mm
            c.drawString(left, y, extra_line)
        y -= 6 * mm

    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 7 * mm

    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    c.drawString(left, y, "Total")
    c.drawRightString(right, y, f"${float(tx.total):,.2f}")
    y -= 10 * mm

    if tx.payments:
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(grey)
        c.drawString(left, y, "PAYMENTS RECEIVED")
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        c.setFillColor(ink)
        for p in tx.payments:
            c.drawString(left, y, f"{p.date.strftime('%d %b %Y')} — {p.method}")
            c.drawRightString(right, y, f"${float(p.amount):,.2f}")
            y -= 5 * mm
        y -= 4 * mm

    balance = float(tx.balance_due)
    c.setStrokeColor(ink)
    c.setLineWidth(1.2)
    c.line(left, y, right, y)
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 13)
    if balance == 0:
        c.setFillColor(green)
        c.drawString(left, y, "PAID IN FULL")
        c.drawRightString(right, y, "$0.00")
    elif balance < 0:
        c.setFillColor(green)
        c.drawString(left, y, "This account is in credit")
        c.drawRightString(right, y, f"${abs(balance):,.2f}")
    else:
        c.setFillColor(rust)
        c.drawString(left, y, "Balance due")
        c.drawRightString(right, y, f"${balance:,.2f}")
    y -= 14 * mm

    payment_bits = [b for b in [
        ("BSB:", biz.payment_bsb) if biz.payment_bsb else None,
        ("Account:", biz.payment_account_number) if biz.payment_account_number else None,
        ("Name:", biz.payment_account_name) if biz.payment_account_name else None,
        ("PayID:", biz.payment_payid) if biz.payment_payid else None,
    ] if b]
    if payment_bits:
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(grey)
        c.drawString(left, y, "PAYMENT DETAILS")
        y -= 5.5 * mm
        c.setFillColor(ink)
        for label, value in payment_bits:
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(left, y, label)
            label_width = stringWidth(label, "Helvetica-Bold", 9.5)
            c.setFont("Helvetica", 9.5)
            c.drawString(left + label_width + 1.5 * mm, y, value)
            y -= 4.5 * mm

    c.showPage()
    c.save()
    return buf.getvalue()


def generate_quote_pdf(quote, biz, theme_key) -> bytes:
    theme = get_theme(theme_key)
    ink = colors.HexColor(theme["ink"])
    accent = colors.HexColor(theme["accent"])
    grey = colors.HexColor("#8A8D85")
    line_col = colors.HexColor(theme["line"])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 20 * mm
    right = width - 20 * mm
    y = height - 20 * mm

    name_y, logo_bottom, meta_y = _draw_header(c, biz, theme_key, left, right, y, "QUOTE")
    c.setFillColor(ink)
    _draw_right_label_value(c, right, meta_y, "Quote #: ", quote.quote_number, 9.5)
    meta_y -= 5 * mm
    _draw_right_label_value(c, right, meta_y, "Issue date: ", quote.date.strftime('%d %b %Y'), 9.5)
    meta_y -= 5 * mm
    if quote.expiry_date:
        _draw_right_label_value(c, right, meta_y, "Valid until: ", quote.expiry_date.strftime('%d %b %Y'), 9.5)
        meta_y -= 5 * mm

    y = min(name_y, logo_bottom, meta_y) - 8 * mm

    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 8 * mm

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "QUOTE FOR")
    y -= 5.5 * mm
    c.setFont("Helvetica-Bold", 11)
    c.setFillColor(ink)
    if quote.contact:
        c.drawString(left, y, quote.contact.display_name)
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        if quote.contact.email:
            c.drawString(left, y, quote.contact.email)
            y -= 4.5 * mm
        if quote.contact.phone:
            c.drawString(left, y, quote.contact.phone)
            y -= 4.5 * mm

    y -= 6 * mm

    amount_col_width = 28 * mm
    desc_max_width = (right - amount_col_width) - left

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(grey)
    c.drawString(left, y, "DESCRIPTION")
    c.drawRightString(right, y, "AMOUNT")
    y -= 3 * mm
    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    c.setFillColor(ink)
    row_shade = colors.HexColor("#F3F2EE")
    for i, li in enumerate(quote.line_items):
        desc_lines = _wrap_text(li.description, "Helvetica", 10, desc_max_width)
        row_height = (4.5 * mm) * (len(desc_lines) - 1) + 6 * mm

        if i % 2 == 1:
            c.setFillColor(row_shade)
            c.rect(left - 3 * mm, y - row_height + 4.5 * mm, (right - left) + 6 * mm, row_height, fill=1, stroke=0)
            c.setFillColor(ink)

        amt = float(li.amount)
        c.drawString(left, y, desc_lines[0])
        c.drawRightString(right, y, f"{'-' if amt < 0 else ''}${abs(amt):,.2f}")
        for extra_line in desc_lines[1:]:
            y -= 4.5 * mm
            c.drawString(left, y, extra_line)
        y -= 6 * mm

    c.setStrokeColor(line_col)
    c.line(left, y, right, y)
    y -= 7 * mm

    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(ink)
    c.drawString(left, y, "Total")
    c.drawRightString(right, y, f"${float(quote.total):,.2f}")
    y -= 12 * mm

    if quote.expiry_date:
        c.setFont("Helvetica-Oblique", 9.5)
        c.setFillColor(accent)
        c.drawString(left, y, f"This quote is valid until {quote.expiry_date.strftime('%d %B %Y')}.")
        y -= 8 * mm

    if quote.notes:
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(grey)
        c.drawString(left, y, "NOTES")
        y -= 5.5 * mm
        c.setFont("Helvetica", 9.5)
        c.setFillColor(ink)
        for line in _wrap_text(quote.notes, "Helvetica", 9.5, right - left):
            c.drawString(left, y, line)
            y -= 4.5 * mm
        y -= 4 * mm

    c.showPage()
    c.save()
    return buf.getvalue()
