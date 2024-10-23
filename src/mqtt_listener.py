import logging
import re

import asyncpg

from src.message import Message
from src.mqtt_client import MqttClient, MqttConfKey

_logger = logging.getLogger(__name__)


class MqttListener(MqttClient):

    def __init__(self, config, database):
        super().__init__(config)

        self._config = config
        self._database = database

        self._subscriptions = set()
        self._skip_subscription_regexes = []
        self._messages: list[Message] = []

        self._pgpool: asyncpg.Pool | None = None

        skip_subscription_regexes = list(
            set(config.get(MqttConfKey.SKIP_SUBSCRIPTION_REGEXES))
        )
        self._skip_subscription_regexes = [
            re.compile(regex) for regex in skip_subscription_regexes
        ]

        subscriptions = config.get(MqttConfKey.SUBSCRIPTIONS)
        valid_subscriptions = [
            sub
            for sub in subscriptions
            if not any(regex.match(sub) for regex in self._skip_subscription_regexes)
        ]
        self._subscriptions = list(set(valid_subscriptions))

    async def subscribe(self):
        if not self._subscriptions:
            return

        subs_qos = 1  # qos for subscriptions, not used, but necessary

        self._pgpool = await asyncpg.create_pool(**self._database)
        async with self._client as client:
            for topic in self._subscriptions:
                await client.subscribe(topic=topic, qos=subs_qos)
                _logger.info("subscribed to MQTT topic (%s)", topic)

            async for message in client.messages:
                _logger.info(
                    "received MQTT topic message (%s: %s)",
                    message.topic,
                    message.payload,
                )

                async with self._pgpool.acquire() as connection:
                    columns = ["topic", "text", "qos", "retain", "time"]
                    record = (
                        str(message.topic),
                        message.payload.decode(),
                        message.qos,
                        message.retain,
                        self._now(),
                    )

                    await connection.copy_records_to_table(
                        "journal", records=[record], columns=columns
                    )

                    _logger.info("overall message: stored=%s", message.payload.decode())
