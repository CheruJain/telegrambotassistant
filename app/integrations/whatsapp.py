"""WhatsApp Business Cloud API integration.

The bot can keep using Telegram exactly as before. Once a WhatsApp Business
number is connected, enable this integration with environment variables and
call ``send_text`` for outbound messages.

No WhatsApp credentials are required until the integration is enabled.
"""
from __future__ import annotations

import json
import logging
import os
from urllib import error, request

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Small provider wrapper so the rest of the bot stays provider-agnostic."""

    def __init__(self) -> None:
        self.enabled = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.graph_api_version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v23.0")

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.access_token and self.phone_number_id)

    def _endpoint(self) -> str:
        return (
            f"https://graph.facebook.com/{self.graph_api_version}/"
            f"{self.phone_number_id}/messages"
        )

    def send_text(self, recipient: str, text: str) -> bool:
        """Send a plain text WhatsApp message.

        Returns False instead of crashing the Telegram bot when WhatsApp is
        not configured or the provider rejects the request.
        """
        if not self.configured:
            logger.info("WhatsApp is not configured; skipping outbound message")
            return False

        phone = "".join(ch for ch in str(recipient) if ch.isdigit())
        if not phone:
            logger.warning("Skipping WhatsApp message: invalid recipient")
            return False

        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._endpoint(),
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                return 200 <= response.status < 300
        except error.HTTPError as exc:
            logger.error("WhatsApp API rejected message: HTTP %s", exc.code)
        except (error.URLError, TimeoutError) as exc:
            logger.error("WhatsApp API request failed: %s", exc)
        except Exception:
            logger.exception("Unexpected WhatsApp integration error")
        return False


whatsapp = WhatsAppService()
