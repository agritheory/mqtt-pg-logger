import logging
import time

from src.app_config import AppConfig
from src.mqtt_listener import MqttListener

logging.getLogger("asyncio").setLevel(logging.INFO)


class Runner:
    def __init__(self, app_config: AppConfig):
        self._mqtt = MqttListener(app_config)

    async def loop(self):
        """endless loop"""
        time_step = 0.05
        await self._mqtt.subscribe()
        time.sleep(time_step)

    async def close(self):
        if self._mqtt is not None:
            self._mqtt = None
