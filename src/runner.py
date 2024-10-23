import logging
import time

from src.app_config import AppConfig
from src.message_store import MessageStore
from src.mqtt_listener import MqttListener

logging.getLogger("asyncio").setLevel(logging.INFO)


class Runner:
    def __init__(self, app_config: AppConfig):
        self._shutdown = False
        self._mqtt = MqttListener(
            app_config.get_mqtt_config(), app_config.get_database_config()
        )
        self._store = MessageStore(app_config.get_database_config())

    async def loop(self):
        """endless loop"""
        time_step = 0.05
        has_messages_to_notify = False

        await self._mqtt.subscribe()
        await self._store.connect()

        time.sleep(time_step)

    async def close(self):
        if self._mqtt is not None:
            self._mqtt = None
        if self._store is not None:
            await self._store.close()
            self._store = None
