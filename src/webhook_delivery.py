"""
Standard Webhooks compliant delivery.

See https://www.standardwebhooks.com/ and
https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md
"""

import base64
import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

import httpx
from standardwebhooks import Webhook

logger = logging.getLogger(__name__)

# Event type for alarm-triggered webhooks (hierarchical, full-stop delimited)
ALARM_TRIGGERED_TYPE = "alarm.triggered"


def build_standard_payload(
	event_type: str,
	data: dict[str, Any],
) -> dict[str, Any]:
	"""Build Standard Webhooks payload: type, timestamp (ISO 8601), data."""
	return {
		"type": event_type,
		"timestamp": datetime.now(timezone.utc).isoformat(),
		"data": data,
	}


def generate_signing_secret() -> str:
	"""Generate a Standard Webhooks signing secret (whsec_<base64>, 32 bytes)."""
	raw = secrets.token_bytes(32)
	b64 = base64.b64encode(raw).decode("ascii")
	return f"whsec_{b64}"


async def deliver_standard_webhook(
	url: str,
	signing_secret: str,
	event_type: str,
	data: dict[str, Any],
	*,
	timeout: float = 15.0,
) -> bool:
	"""
	Deliver a webhook using Standard Webhooks spec.

	- Payload: type, timestamp (ISO 8601), data
	- Headers: webhook-id, webhook-timestamp, webhook-signature
	- HMAC-SHA256 signature over msg_id.timestamp.payload
	"""
	payload = build_standard_payload(event_type, data)
	payload_str = json.dumps(payload, separators=(",", ":"))  # Minified, stable

	msg_id = f"msg_{secrets.token_urlsafe(24)}"
	timestamp = datetime.now(timezone.utc)

	wh = Webhook(signing_secret)
	signature = wh.sign(msg_id=msg_id, timestamp=timestamp, data=payload_str)

	headers = {
		"Content-Type": "application/json",
		"webhook-id": msg_id,
		"webhook-timestamp": str(int(timestamp.timestamp())),
		"webhook-signature": signature,
	}

	try:
		async with httpx.AsyncClient() as client:
			resp = await client.post(url, content=payload_str, headers=headers, timeout=timeout)
		if 200 <= resp.status_code < 300:
			logger.info("Webhook delivered successfully to %s", url)
			return True
		logger.warning(
			"Webhook delivery returned %s for %s: %s",
			resp.status_code,
			url,
			resp.text[:200],
		)
		return False
	except Exception as e:
		logger.error("Webhook delivery failed for %s: %s", url, e)
		return False
