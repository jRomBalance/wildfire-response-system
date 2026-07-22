"""
WildfireNet - SMTP Email Notifier (Microsoft 365 / Any SMTP)
Replaces SendGrid with standard SMTP - works with Microsoft 365,
Gmail, or any SMTP provider.
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
    Sends HTML email alerts via SMTP.
    Works with Microsoft 365, Gmail, or any SMTP provider.

    Required env vars:
        SMTP_HOST      (e.g. smtp.office365.com)
        SMTP_PORT      (e.g. 587)
        SMTP_USER      (e.g. jerry@romallen.com)
        SMTP_PASSWORD  (your email password or app password)
        ALERT_FROM_EMAIL (display from address)
    """

    def __init__(self):
        self.host     = os.getenv("SMTP_HOST", "smtp.office365.com")
        self.port     = int(os.getenv("SMTP_PORT", "587"))
        self.user     = os.getenv("SMTP_USER", "")
        self.password = os.getenv("SMTP_PASSWORD", "")
        self.from_email = os.getenv("ALERT_FROM_EMAIL", self.user)

        if not self.user or not self.password:
            raise ValueError(
                "SMTP credentials missing.\n"
                "Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD in Railway Variables."
            )

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        name: Optional[str] = None,
        plain_text: Optional[str] = None,
    ) -> bool:
        """Send HTML email asynchronously via SMTP."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._send_sync, to, subject, body, plain_text
        )

    def _send_sync(
        self,
        to: str,
        subject: str,
        body: str,
        plain_text: Optional[str] = None,
    ) -> bool:
        """Synchronous SMTP send (runs in thread pool)."""
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"WildfireNet Alerts <{self.from_email}>"
            msg["To"]      = to

            # Plain text fallback
            text_part = MIMEText(
                plain_text or "WildfireNet Alert - see HTML version for details.",
                "plain"
            )
            html_part = MIMEText(body, "html")

            msg.attach(text_part)
            msg.attach(html_part)

            with smtplib.SMTP(self.host, self.port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self.user, self.password)
                server.sendmail(self.from_email, to, msg.as_string())

            logger.info(f"Email sent to {to} via SMTP | Subject: {subject}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP auth failed for {to}: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending to {to}: {e}")
            return False
        except Exception as e:
            logger.error(f"Email failed to {to}: {e}")
            return False

    async def send_bulk(
        self,
        contacts: list,
        subject: str,
        body: str,
    ) -> dict:
        """Send to multiple contacts concurrently."""
        results = await asyncio.gather(
            *[self.send(to=c["email"], subject=subject, body=body)
              for c in contacts],
            return_exceptions=True,
        )
        sent   = [c["email"] for c, r in zip(contacts, results) if r is True]
        failed = [c["email"] for c, r in zip(contacts, results) if r is not True]
        logger.info(f"Bulk email: {len(sent)} sent, {len(failed)} failed")
        return {"sent": sent, "failed": failed}
