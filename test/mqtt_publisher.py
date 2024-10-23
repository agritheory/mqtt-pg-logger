import logging
import random

from src.mqtt_client import MqttClient

_logger = logging.getLogger(__name__)


class MqttPublisher(MqttClient):

    def __init__(self, config):
        super().__init__(config)

    @classmethod
    def get_default_client_id(cls):
        return f"pg_log_test_{random.randint(1, 9999999999)}"

    async def publish(self, topic: str, payload: str):
        async with self._client as client:
            print("client: ", client)
            result = await client.publish(
                topic=topic, payload=payload, qos=2, retain=False
            )
            print("pub result: ", result)
            return result

    def _on_publish(self, client, userdata, mid, reason_codes, properties):
        """MQTT callback is invoked when message was successfully sent to the MQTT server."""
        _logger.debug("published MQTT message %s", str(mid))
