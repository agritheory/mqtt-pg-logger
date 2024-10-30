from src.app_config import AppConfig
from src.mqtt_client import MqttClient


class MqttPublisher(MqttClient):
	def __init__(self, config: AppConfig):
		super().__init__(config)

	async def publish(self, topic: str, payload: str):
		async with self._client as client:
			await client.publish(topic=topic, payload=payload, qos=2, retain=False)
