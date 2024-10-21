import logging
import time

from src.app_config import AppConfig
from src.lifecycle_control import LifecycleControl, StatusNotification
from src.message_store import MessageStore
from src.mqtt_listener import MqttListener

_logger = logging.getLogger(__name__)
logging.getLogger("asyncio").setLevel(logging.INFO)


class Runner:
    def __init__(self, app_config: AppConfig):
        self._shutdown = False
        self._store = MessageStore(app_config.get_database_config())
        self._mqtt = MqttListener(app_config.get_mqtt_config())
        self._mqtt.connect()

    async def loop(self):
        """endless loop"""
        time_step = 0.05
        has_messages_to_notify = False

        await self._store.connect()

        try:
            while LifecycleControl.should_proceed():
                # if not self._store.is_alive():
                #     raise RuntimeError("database thread was finished! abort.")

                messages = self._mqtt.get_messages()
                if messages:
                    has_messages_to_notify = True
                    await self._store.queue(messages)

                if len(messages) == 0:
                    # not busy
                    await self._store.clean_up()
                    self._mqtt.ensure_connection()

                    if has_messages_to_notify:
                        has_messages_to_notify = False
                        LifecycleControl.notify(
                            StatusNotification.RUNNER_QUEUE_EMPTIED
                        )  # test related

                    time.sleep(time_step)
        except KeyboardInterrupt:
            # gets called without signal-handler
            _logger.debug("finishing...")

    async def close(self):
        if self._mqtt is not None:
            self._mqtt.close()
            self._mqtt = None
        if self._store is not None:
            await self._store.close()
            self._store = None
