"""WhatsApp Business Cloud API integration."""
from __future__ import annotations

import json
import logging
import os
from urllib import error, parse, request

logger = logging.getLogger(__name__)


class WhatsAppService:
    """Provider wrapper for outbound WhatsApp Business Cloud API messages.

    The sender can be configured either with WHATSAPP_PHONE_NUMBER_ID directly
    or by providing the business sender number plus WHATSAPP_WABA_ID. In the
    latter case the phone number ID is resolved automatically from Meta.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("WHATSAPP_ENABLED", "false").lower() == "true"
        self.access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.business_number = os.getenv("WHATSAPP_BUSINESS_NUMBER", "")
        self.waba_id = os.getenv("WHATSAPP_WABA_ID", "")
        self.graph_api_version = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v23.0")
        self._resolved_phone_number_id = ""

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled
            and self.access_token
            and (self.phone_number_id or (self.business_number and self.waba_id))
        )

    def _endpoint(self) -> str:
        phone_number_id = self._get_phone_number_id()
        return (
            f"https://graph.facebook.com/{self.graph_api_version}/"
            f"{phone_number_id}/messages"
        )

    def _get_phone_number_id(self) -> str:
        if self.phone_number_id:
            return self.phone_number_id
        if self._resolved_phone_number_id:
            return self._resolved_phone_number_id
        if not self.access_token or not self.waba_id or not self.business_number:
            return ""

        fields = "id,display_phone_number"
        url = (
            f"https://graph.facebook.com/{self.graph_api_version}/"
            f"{self.waba_id}/phone_numbers?fields={parse.quote(fields)}&limit=100"
        )
        req = request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            target = self._phone(self.business_number)
            for item in data.get("data", []):
                if self._phone(item.get("display_phone_number", "")) == target:
                    self._resolved_phone_number_id = str(item.get("id") or "")
                    return self._resolved_phone_number_id
            logger.error("WhatsApp sender number was not found in the configured WABA")
        except Exception:
            logger.exception("Could not resolve WhatsApp phone number ID from Meta")
        return ""

    @staticmethod
    def _phone(recipient: str) -> str:
        return "".join(ch for ch in str(recipient) if ch.isdigit())

    def send_text(self, recipient: str, text: str) -> bool:
        if not self.configured or not self._get_phone_number_id():
            logger.info("WhatsApp is not configured; skipping outbound message")
            return False
        phone = self._phone(recipient)
        if not phone:
            logger.warning("Skipping WhatsApp message: invalid recipient")
            return False
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        return self._post(payload)

    def send_template(self, recipient: str, template_name: str, parameters: list[str]) -> bool:
        """Send an approved WhatsApp template message."""
        if not self.configured or not self._get_phone_number_id():
            logger.info("WhatsApp is not configured; skipping template message")
            return False
        phone = self._phone(recipient)
        if not phone or not template_name:
            logger.warning("Skipping WhatsApp template: invalid recipient/template")
            return False
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": str(value)} for value in parameters],
                    }
                ],
            },
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        phone_number_id = self._get_phone_number_id()
        if not phone_number_id:
            return False
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"https://graph.facebook.com/{self.graph_api_version}/{phone_number_id}/messages",
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
