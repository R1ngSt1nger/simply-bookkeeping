import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.utils import formataddr

from . import control_models

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "simply-bookkeeping-logo.png")


class SMTPNotConfigured(Exception):
    pass


def is_smtp_configured(settings: control_models.AppSettings) -> bool:
    return bool(settings and settings.smtp_enabled and settings.smtp_host and settings.smtp_from_email)


def send_email(settings: control_models.AppSettings, to_email: str, subject: str, html_body: str, embed_logo: bool = True):
    """Send an HTML email over the configured SMTP server, with the app logo
    embedded inline (not linked by URL, since a self-hosted instance usually
    isn't reachable from wherever the recipient's mail client is)."""
    if not is_smtp_configured(settings):
        raise SMTPNotConfigured("SMTP is not enabled or not fully configured.")

    from_name = settings.smtp_from_name or "Simply Bookkeeping"
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, settings.smtp_from_email))
    msg["To"] = to_email

    body_html = html_body
    alt = MIMEMultipart("alternative")
    if embed_logo and os.path.exists(LOGO_PATH):
        body_html = body_html.replace("__LOGO_CID__", "cid:app-logo")
    else:
        body_html = body_html.replace("__LOGO_CID__", "")
    alt.attach(MIMEText(body_html, "html"))
    msg.attach(alt)

    if embed_logo and os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as f:
            logo = MIMEImage(f.read())
        logo.add_header("Content-ID", "<app-logo>")
        logo.add_header("Content-Disposition", "inline", filename="logo.png")
        msg.attach(logo)

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
