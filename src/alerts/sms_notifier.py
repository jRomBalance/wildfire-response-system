"""
WildfireNet — SMS Notifier (Twilio)
=====================================
Sends SMS alerts directly to firefighter units.
Fast, reliable, works when internet is spotty.

Get Twilio account: https://www.twilio.com/try-twilio
Free trial includes $15 credit — enough for hundreds of test SMS.
"""

import os
import logging
import asyncio
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class SMSNotifier:
    """
    Sends SMS via Twilio to firefighter contacts.

    Required env vars:
        TWILIO_ACCOUNT_SID
        TWILIO_AUTH_TOKEN
        TWILIO_FROM_NUMBER  (your Twilio phone number, e.g. +15551234567)
    """

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number = os.getenv("TWILIO_FROM_NUMBER")

        if not all([self.account_sid, self.auth_token, self.from_number]):
            raise ValueError(
                "Twilio credentials missing.\n"
                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER in .env\n"
                "Get free account: https://www.twilio.com/try-twilio"
            )

    async def send(self, to: str, message: str) -> bool:
        """
        Send an SMS message asynchronously.

        Args:
            to:      Recipient phone number (E.164 format: +15551234567)
            message: SMS body text (max 1600 chars; splits into segments if longer)

        Returns:
            True if sent successfully, False otherwise.
        """
        # Run Twilio's sync client in a thread pool to keep async-friendly
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_sync, to, message)

    def _send_sync(self, to: str, message: str) -> bool:
        """Synchronous Twilio send (runs in thread pool)."""
        try:
            from twilio.rest import Client
            client = Client(self.account_sid, self.auth_token)

            msg = client.messages.create(
                body=message,
                from_=self.from_number,
                to=to,
            )

            logger.info(f"SMS sent to {to} | SID: {msg.sid} | Status: {msg.status}")
            return True

        except ImportError:
            logger.error("twilio package not installed. Run: pip install twilio")
            return False
        except Exception as e:
            logger.error(f"SMS failed to {to}: {e}")
            return False

    async def send_bulk(self, contacts: list[str], message: str) -> dict:
        """
        Send the same message to multiple contacts concurrently.

        Returns:
            dict with 'sent' and 'failed' lists of phone numbers.
        """
        results = await asyncio.gather(
            *[self.send(to=phone, message=message) for phone in contacts],
            return_exceptions=True,
        )

        sent, failed = [], []
        for phone, result in zip(contacts, results):
            if result is True:
                sent.append(phone)
            else:
                failed.append(phone)

        logger.info(f"Bulk SMS: {len(sent)} sent, {len(failed)} failed")
        return {"sent": sent, "failed": failed}