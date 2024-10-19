import asyncio
import datetime
import logging
import threading
import time
from collections import deque

from tzlocal import get_localzone

from src.database import DatabaseConfKey
from src.message import Message
from src.message_store import MessageStore

_logger = logging.getLogger(__name__)


class ProxyStore(threading.Thread):
    """An async proxy to MessageStore which handles batches, queuing"""

    RECONNECT_AFTER_SECONDS = 3600

    DEFAULT_BATCH_SIZE = 100
    DEFAULT_WAIT_MAX_SECONDS = 10

    QUEUE_LIMIT = 50000
    WAIT_AFTER_ERROR_SECONDS = 20
    FORCE_CLEAN_UP_AFTER_SECONDS = 3000
    LAZY_CLEAN_UP_AFTER_SECONDS = 300

    def __init__(self, config):
        threading.Thread.__init__(self)

        # runtime properties
        self._message_store = MessageStore(config)
        self._closing = False
        self._lock = threading.Lock()
        self._messages = deque()
        self._write_immediately = False

        self._last_error_text = None

        # configuration
        self._batch_size = min(
            config.get(DatabaseConfKey.BATCH_SIZE, self.DEFAULT_BATCH_SIZE), 10000
        )
        self._wait_max_seconds = min(
            config.get(DatabaseConfKey.WAIT_MAX_SECONDS, self.DEFAULT_WAIT_MAX_SECONDS),
            60,
        )

        super().start()

    def close(self):
        with self._lock:
            self._closing = True

    def _is_closing(self):
        with self._lock:
            return bool(self._closing)

    def queue(self, messages: list[Message], write_immediately=False):
        added = 0
        lost_messages = None

        with self._lock:
            if write_immediately and not self._write_immediately:
                self._write_immediately = True

            for message in messages:
                if len(self._messages) > self.QUEUE_LIMIT:
                    lost_messages = len(messages) - added
                    break
                self._messages.append(message)
                added += 1

        if lost_messages is not None:
            _logger.error(
                "message queue limit (%d) reached => lost %d messages!",
                self.QUEUE_LIMIT,
                lost_messages,
            )

    async def _close_connection(self):
        try:
            await self._message_store.close()
        except Exception as ex:
            _logger.exception(ex)

    def start(self):
        raise RuntimeError("started within constructor!")

    def run(self):
        step_time = 0.05

        try:
            while not self._is_closing():
                busy = False

                conn_task = asyncio.create_task(self._check_connection())
                if conn_task.result():
                    busy = True
                if self._should_store_messages():
                    asyncio.create_task(self._store_messages())
                    # TODO: only set busy if messages need to be stored
                    busy = True
                if not busy:
                    if self._clean_up():
                        busy = True

                if self._message_store.last_connect_time is not None:
                    diff_seconds = (
                        self._now() - self._message_store.last_connect_time
                    ).total_seconds()
                    if diff_seconds > self.RECONNECT_AFTER_SECONDS:
                        _logger.debug(
                            f"automatically closing connection after {self.RECONNECT_AFTER_SECONDS}s."
                        )
                        asyncio.create_task(self._close_connection())
                        busy = True

                time.sleep(step_time / 100 if busy else step_time)

        except Exception as ex:
            # stop thread / break loop => shutdown service => restart via systemd after 15 (?) seconds
            _logger.exception(ex)
            self.close()
        finally:
            asyncio.create_task(self._close_connection())

    async def _check_connection(self) -> bool:
        """Separated to mock and test without threads"""

        if self._message_store.is_connected:
            return False
        await self._message_store.connect()
        return True

    def _clean_up(self):
        """Separated to mock and test without threads"""
        if self._should_clean_up_items():
            self._message_store.clean_up()
            return True
        return False

    def _should_store_messages(self) -> bool:
        message_count = len(self._messages)
        if message_count == 0:
            return False

        if self._write_immediately:
            return True

        if message_count >= self._batch_size:
            return True

        diff_seconds = (
            self._now() - self._message_store.last_store_time
        ).total_seconds()
        if diff_seconds > self._wait_max_seconds:
            return True

        return False

    async def _store_messages(self) -> bool:
        messages = []

        with self._lock:
            while len(messages) < self._batch_size:
                try:
                    m = self._messages.popleft()
                    messages.append(m)
                except IndexError:
                    self._write_immediately = False
                    break

        if messages:
            await self._message_store.store(messages)

        self._last_error_text = None

        return bool(messages)

    def _should_clean_up_items(self) -> bool:
        seconds_clean_up = (
            self._now() - self._message_store.last_clean_up_time
        ).total_seconds()
        if seconds_clean_up >= self.FORCE_CLEAN_UP_AFTER_SECONDS:
            return True

        if (
            len(self._messages) == 0
            and seconds_clean_up > self.LAZY_CLEAN_UP_AFTER_SECONDS
        ):
            seconds_since_last_store = (
                self._now() - self._message_store.last_store_time
            ).total_seconds()
            return bool(seconds_since_last_store > 1)

        return False

    @classmethod
    def _now(cls) -> datetime:
        """overwritable `datetime.now` for testing"""
        return datetime.datetime.now(tz=get_localzone())
