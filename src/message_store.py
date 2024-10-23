import datetime
import logging

from src.database import Database, DatabaseConfKey
from src.lifecycle_control import LifecycleControl, StatusNotification
from src.message import Message

_logger = logging.getLogger(__name__)


class MessageStore(Database):

    DEFAULT_BATCH_SIZE = 100
    DEFAULT_WAIT_MAX_SECONDS = 10
    DEFAULT_CLEAN_UP_AFTER_DAYS = 14
    QUEUE_LIMIT = 50000

    FORCE_CLEAN_UP_AFTER_SECONDS = 3000
    LAZY_CLEAN_UP_AFTER_SECONDS = 300

    def __init__(self, config):
        super().__init__(config)

        self._batch_size = max(
            config.get(DatabaseConfKey.BATCH_SIZE, self.DEFAULT_BATCH_SIZE), 10000
        )
        self._clean_up_after_days = config.get(
            DatabaseConfKey.CLEAN_UP_AFTER_DAYS, self.DEFAULT_CLEAN_UP_AFTER_DAYS
        )

        self._messages: list[Message] = []

        self._last_clean_up_time = self._now()
        self._last_connect_time = None
        self._last_store_time = self._now()

        self._status_stored_message_count = 0
        self._status_last_log = self._now()

    async def connect(self):
        await super().connect()
        LifecycleControl.notify(StatusNotification.MESSAGE_STORE_CONNECTED)

    async def close(self):
        was_connection = bool(self._pool)
        await super().close()
        if was_connection:
            LifecycleControl.notify(StatusNotification.MESSAGE_STORE_CLOSED)

    @property
    def last_clean_up_time(self) -> datetime.datetime | None:
        return self._last_clean_up_time

    @property
    def last_connect_time(self) -> datetime.datetime | None:
        return self._last_connect_time

    @property
    def last_store_time(self) -> datetime.datetime | None:
        return self._last_store_time

    async def store(self, message: Message):
        if not message:
            return

        columns = ["topic", "text", "qos", "retain", "time"]
        record = tuple(getattr(message, column) for column in columns)

        async with self._pool.acquire() as connection:
            await connection.copy_records_to_table(
                self._table_name, records=[record], columns=columns
            )

            if (
                _logger.isEnabledFor(logging.INFO)
                and (self._now() - self._status_last_log).total_seconds() > 300
            ):
                self._status_last_log = self._now()
                _logger.info("overall message: stored=%d", message.text)

            LifecycleControl.notify(StatusNotification.MESSAGE_STORE_STORED)

        self._messages.remove(message)
