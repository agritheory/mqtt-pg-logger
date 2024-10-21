import asyncio
import datetime
import logging

from psycopg import sql

from src.database import Database, DatabaseConfKey
from src.lifecycle_control import LifecycleControl, StatusNotification
from src.message import Message

_logger = logging.getLogger(__name__)


class MessageStore(Database):

    DEFAULT_BATCH_SIZE = 100
    DEFAULT_WAIT_MAX_SECONDS = 10
    DEFAULT_CLEAN_UP_AFTER_DAYS = 14
    QUEUE_LIMIT = 50000

    def __init__(self, config):
        super().__init__(config)

        self._batch_size = max(
            config.get(DatabaseConfKey.BATCH_SIZE, self.DEFAULT_BATCH_SIZE), 10000
        )
        self._clean_up_after_days = config.get(
            DatabaseConfKey.CLEAN_UP_AFTER_DAYS, self.DEFAULT_CLEAN_UP_AFTER_DAYS
        )

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

    async def queue(self, messages: list[Message]):
        added = 0
        lost_messages = 0

        async with asyncio.Lock():
            for message in messages:
                if len(messages) > self.QUEUE_LIMIT:
                    lost_messages = len(messages) - added
                    break
                await self.store(message)
                added += 1

        if lost_messages > 0:
            _logger.error(
                "message queue limit (%d) reached => lost %d messages!",
                self.QUEUE_LIMIT,
                lost_messages,
            )

    def clean_up(self):
        if self._clean_up_after_days <= 0:
            return  # skip

        time_limit = self._now() - datetime.timedelta(days=self._clean_up_after_days)

        delete_statement = sql.SQL(
            "DELETE FROM {table} WHERE time < {time_limit}"
        ).format(
            table=sql.Identifier(self._table_name), time_limit=sql.Literal(time_limit)
        )

        with self._connection.cursor() as cursor:
            cursor.execute(delete_statement)
            cursor_rowcount = cursor.rowcount

        _logger.info("clean up: %d row(s) deleted", cursor_rowcount)

        self._last_clean_up_time = self._now()
