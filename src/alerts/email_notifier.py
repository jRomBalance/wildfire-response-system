"""
WildfireNet — Email Notifier (SendGrid)
=========================================
Sends HTML email alerts to fire stations and agency contacts.
SendGrid free tier: 100 emails/day — enough for development.

Get SendGrid account: https://signup.sendgrid.com/
"""

import os
import logging
import asyncio
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class EmailNotifier:
    """
    Sends HTML email alerts via SendGrid.

    Required env vars:
        SENDGRID_API_KEY
        ALERT_FROM_EMAIL  (verified sender in SendGrid, e.g. alerts@wildfirenet.dev)
    """

    def __init__(self):
        self.api_key = os.getenv("SENDGRID_API_KEY")
        self.from_email = os.getenv("ALERT_FROM_EMAIL", "alerts@wildfirenet.dev")

        if not self.api_key:
            raise ValueError(
                "SendGrid API key missing.\n"
                "Set SENDGRID_API_KEY in .env\n"
                "Get free account: https://signup.sendgrid.com/"
            )

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        name: Optional[str] = None,
        plain_text: Optional[str] = None,
    ) -> bool:
        """
        Send an HTML email asynchronously.

        Args:
            to: Recipient email address
            subject: Email subject line
            body: HTML email body
            name: Recipient display name (optional)
            plain_text: Plain text fallback (optional)

        Returns:
            True if sent successfully, False otherwise.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._send_sync, to, subject, body, name, plain_text
        )

    def _send_sync(
        self,
        to: str,
        subject: str,
        body: str,
        name: Optional[str],
        plain_text: Optional[str],
    ) -> bool:
        """Synchronous SendGrid send (runs in thread pool)."""
        try:
            from sendgrid import SendGridAPIClient
            from sendgrid.helpers.mail import (
                Mail,
                To,
                From,
                Subject,
                HtmlContent,
                PlainTextContent,
            )

            to_obj = To(email=to, name=name) if name else To(email=to)

            message = Mail(
                from_email=From(self.from_email, "WildfireNet Alerts"),
                to_emails=to_obj,
                subject=Subject(subject),
                html_content=HtmlContent(body),
            )

            if plain_text:
                message.plain_text_content = PlainTextContent(plain_text)

            sg = SendGridAPIClient(self.api_key)
            response = sg.send(message)

            logger.info(
                f"Email sent to {to} | Status: {response.status_code} | Subject: {subject}"
            )
            return response.status_code in (200, 201, 202)

        except ImportError:
            logger.error("sendgrid package not installed. Run: pip install sendgrid")
            return False
        except Exception as e:
            logger.error(f"Email failed to {to}: {e}")
            return False

    async def send_bulk(
        self,
        contacts: list[dict],
        subject: str,
        body: str,
    ) -> dict:
        """
        Send to multiple contacts concurrently.

        Args:
            contacts: List of dicts with 'email' and optional 'name' keys
            subject: Email subject
            body: HTML body

        Returns:
            dict with 'sent' and 'failed' lists.
        """
        results = await asyncio.gather(
            *[
                self.send(
                    to=c["email"],
                    subject=subject,
                    body=body,
                    name=c.get("name"),
                )
                for c in contacts
            ],
            return_exceptions=True,
        )

        sent, failed = [], []
        for contact, result in zip(contacts, results):
            if result is True:
                sent.append(contact["email"])
            else:
                failed.append(contact["email"])

        logger.info(f"Bulk email: {len(sent)} sent, {len(failed)} failed")
        return {"sent": sent, "failed": failed}