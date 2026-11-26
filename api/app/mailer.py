"""Outgoing email over SMTP (invitations). Optional: without SMTP the
admin panel shows the link so the owner can send it by hand."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


def enabled() -> bool:
    s = get_settings()
    return bool(s.smtp_host and (s.smtp_from or s.smtp_user))


def send(to: str, subject: str, text: str) -> bool:
    s = get_settings()
    if not enabled():
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from or s.smtp_user, to, subject
    msg.set_content(text)
    try:
        ctx = ssl.create_default_context()
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=20) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                if s.smtp_tls:
                    smtp.starttls(context=ctx)
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        return True
    except Exception as e:     # never break the panel because email failed
        log.warning("smtp send failed: %s", type(e).__name__)
        return False


def invitation(to: str, link: str, days: int) -> bool:
    return send(to, "Te invitaron a Clipeo",
                f"Hola:\n\nTe invitaron a Clipeo, la app para sacar clips verticales con subtítulos.\n\n"
                f"Crea tu cuenta con este enlace (sirve una sola vez y caduca en {days} días):\n{link}\n\n"
                "Si no esperabas este correo, ignóralo.\n")
