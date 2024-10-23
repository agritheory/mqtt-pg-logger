import asyncio
import logging
import re

from src.app_config import AppConfig
from src.constants import MqttConfKey
from src.database import Database
from src.mqtt_client import MqttClient

_logger = logging.getLogger(__name__)


class MqttListener(MqttClient):

    def __init__(self, config: AppConfig):
        super().__init__(config)

        self._mqtt = config.get_mqtt_config()
        self._database = Database(config.get_database_config())

        skip_subscription_regexes = list(
            set(self._mqtt.get(MqttConfKey.SKIP_SUBSCRIPTION_REGEXES))
        )
        self._skip_subscription_regexes = [
            re.compile(regex) for regex in skip_subscription_regexes
        ]

        subscriptions = self._mqtt.get(MqttConfKey.SUBSCRIPTIONS)
        valid_subscriptions = [
            sub
            for sub in subscriptions
            if not any(regex.match(sub) for regex in self._skip_subscription_regexes)
        ]
        self._subscriptions = list(set(valid_subscriptions))

    async def listen(self):
        if not self._subscriptions:
            return

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self.process())

    async def process(self):
        subs_qos = 1  # qos for subscriptions, not used, but necessary
        async with self._client as client:
            await self._database.connect()

            for topic in self._subscriptions:
                await client.subscribe(topic=topic, qos=subs_qos)
                _logger.info("subscribed to MQTT topic (%s)", topic)

            async for message in client.messages:
                _logger.info(
                    "received MQTT topic message (%s: %s)",
                    message.topic,
                    message.payload,
                )

                async with self._database._pool.acquire() as connection:
                    columns = ["topic", "text", "qos", "retain", "time"]
                    record = (
                        str(message.topic),
                        message.payload.decode(),
                        message.qos,
                        message.retain,
                        self._database._now(),
                    )

                    await connection.copy_records_to_table(
                        self._database._table_name, records=[record], columns=columns
                    )

                    _logger.info("overall message: stored=%s", message.payload.decode())
