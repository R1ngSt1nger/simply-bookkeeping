import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication
from email.utils import formataddr

from . import control_models

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "simply-bookkeeping-logo.png")


class SMTPNotConfigured(Exception):
    pass


def is_smtp_configured(settings: control_models.AppSettings) -> bool:
    return bool(settings and settings.smtp_enabled and settings.smtp_host and settings.smtp_from_email)


def send_email(settings: control_models.AppSettings, to_email: str, subject: str, html_body: str, embed_logo: bool = True, attachments: list = None):
    """Send an HTML email over the configured SMTP server, with the app logo
    embedded inline (not linked by URL, since a self-hosted instance usually
    isn't reachable from wherever the recipient's mail client is).

    attachments: optional list of (filename, bytes) tuples, e.g. a generated
    quote or invoice PDF, attached as real file attachments (not inline)."""
    if not is_smtp_configured(settings):
        raise SMTPNotConfigured("SMTP is not enabled or not fully configured.")

    from_name = settings.smtp_from_name or "Simply Bookkeeping"

    body = MIMEMultipart("related")
    body_html = html_body
    if embed_logo and os.path.exists(LOGO_PATH):
        body_html = body_html.replace("__LOGO_CID__", "cid:app-logo")
    else:
        body_html = body_html.replace("__LOGO_CID__", "")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_html, "html"))
    body.attach(alt)

    if embed_logo and os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as f:
            logo = MIMEImage(f.read())
        logo.add_header("Content-ID", "<app-logo>")
        logo.add_header("Content-Disposition", "inline", filename="logo.png")
        body.attach(logo)

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(body)
        for filename, file_bytes in attachments:
            part = MIMEApplication(file_bytes, _subtype="pdf")
            part.add_header("Content-Disposition", "attachment", filename=filename)
            msg.attach(part)
    else:
        msg = body

    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, settings.smtp_from_email))
    msg["To"] = to_email

    port = settings.smtp_port or 587
    encryption = (settings.smtp_encryption or "starttls").lower()

    if encryption == "ssl":
        server = smtplib.SMTP_SSL(settings.smtp_host, port, timeout=15)
    else:
        server = smtplib.SMTP(settings.smtp_host, port, timeout=15)

    try:
        if encryption == "starttls":
            server.starttls()
        if settings.smtp_username and settings.smtp_password:
            server.login(settings.smtp_username, settings.smtp_password)
        server.sendmail(settings.smtp_from_email, [to_email], msg.as_string())
    finally:
        server.quit()
