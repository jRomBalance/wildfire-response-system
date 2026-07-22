"""
WildfireNet - Email Notifier
=============================
Tries SendGrid first (if SENDGRID_API_KEY is set).
Falls back to SMTP (Microsoft 365, Bluehost, Gmail, any SMTP).

SendGrid env vars:
    SENDGRID_API_KEY
    ALERT_FROM_EMAIL

SMTP env vars:
    SMTP_HOST      (e.g. mail.romallen.com or smtp.office365.com)
    SMTP_PORT      (587)
    SMTP_USER      (jerry@romallen.com)
    SMTP_PASSWORD  (your email password)
    ALERT_FROM_EMAIL
"""

import os
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class EmailNotifier:
    """
    Sends HTML email alerts.
    Uses SendGrid if SENDGRID_API_KEY is set, otherwise SMTP.
    """

    def __init__(self):
        self.sendgrid_key  = os.getenv("SENDGRID_API_KEY", "")
        self.from_email    = os.getenv("ALERT_FROM_EMAIL", "alerts@romallen.com")
        self.smtp_host     = os.getenv("SMTP_HOST", "mail.romallen.com")
        self.smtp_port     = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user     = os.getenv("SMTP_USER", self.from_email)
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")

        self.use_sendgrid = bool(self.sendgrid_key and self.sendgrid_key.startswith("SG."))

        if self.use_sendgrid:
            logger.info("Email: using SendGrid")
        elif self.smtp_password:
            logger.info(f"Email: using SMTP ({self.smtp_host})")
        else:
            logger.warning("Email: no credentials configured")

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        name: Optional[str] = None,
        plain_text: Optional[str] = None,
    ) -> bool:
        """Send HTML email - tries SendGrid first, falls back to SMTP."""
        if self.use_sendgrid:
            result = await self._send_sendgrid(to, subject, body, name, plain_text)
            if result:
                return True
            logger.warning("SendGrid failed - trying SMTP fallback")

        if self.smtp_password:
            return await self._send_smtp(to, subject, body, plain_text)

        logger.error("No working email method configured")
        return False

    async def _send_sendgrid(self, to, subject, body, name, plain_text) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sendgrid_sync, to, subject, body, name, plain_text
        )

    def _sendgrid_sync(self, to, subject, body, name, plain_text) -> bool:
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import Mail, To, From, Subject, HtmlContent

            to_obj  = To(email=to, name=name) if name else To(email=to)
            message = Mail(
                from_email=From(self.from_email, "WildfireNet Alerts"),
                to_emails=to_obj,
                subject=Subject(subject),
                html_content=HtmlContent(body),
            )
            sg       = SendGridAPIClient(self.sendgrid_key)
            response = sg.send(message)
            success  = response.status_code in (200, 201, 202)
            if success:
                logger.info(f"SendGrid: sent to {to} | status {response.status_code}")
            else:
                logger.error(f"SendGrid: failed to {to} | status {response.status_code} | body {response.body}")
            return success
        except ImportError:
            logger.warning("sendgrid package not installed")
            return False
        except Exception as e:
            logger.error(f"SendGrid error to {to}: {e}")
            return False

    async def _send_smtp(self, to, subject, body, plain_text) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._smtp_sync, to, subject, body, plain_text
        )

    def _smtp_sync(self, to, subject, body, plain_text) -> bool:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"WildfireNet Alerts <{self.from_email}>"
            msg["To"]      = to

            msg.attach(MIMEText(
                plain_text or "WildfireNet Alert - open HTML version for details.",
                "plain"
            ))
            msg.attach(MIMEText(body, "html"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to, msg.as_string())

            logger.info(f"SMTP: sent to {to} via {self.smtp_host}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP auth failed: {e}")
            return False
        except Exception as e:
            logger.error(f"SMTP error to {to}: {e}")
            return False

    async def send_bulk(self, contacts: list, subject: str, body: str) -> dict:
        """Send to multiple contacts concurrently."""
        results = await asyncio.gather(
            *[self.send(to=c["email"], subject=subject, body=body) for c in contacts],
            return_exceptions=True,
        )
        sent   = [c["email"] for c, r in zip(contacts, results) if r is True]
        failed = [c["email"] for c, r in zip(contacts, results) if r is not True]
        logger.info(f"Bulk email: {len(sent)} sent, {len(failed)} failed")
        return {"sent": sent, "failed": failed}