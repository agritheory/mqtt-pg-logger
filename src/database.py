import abc
import datetime
import logging

import asyncpg
from tzlocal import get_localzone


_logger = logging.getLogger(__name__)


class DatabaseConfKey:
    HOST = "host"
    USER = "user"
    PORT = "port"
    PASSWORD = "password"
    DATABASE = "database"
    TABLE_NAME = "table_name"
    TIMEZONE = "timezone"

    BATCH_SIZE = "batch_size"
    WAIT_MAX_SECONDS = "wait_max_seconds"
    CLEAN_UP_AFTER_DAYS = "clean_up_after_days"


DATABASE_JSONSCHEMA = {
    "type": "object",
    "properties": {
        DatabaseConfKey.HOST: {
            "type": "string",
            "minLength": 1,
            "description": "Database host",
        },
        DatabaseConfKey.PORT: {
            "type": "integer",
            "minimum": 1,
            "description": "Database port",
        },
        DatabaseConfKey.USER: {
            "type": "string",
            "minLength": 1,
            "description": "Database user",
        },
        DatabaseConfKey.PASSWORD: {
            "type": "string",
            "minLength": 1,
            "description": "Database password",
        },
        DatabaseConfKey.DATABASE: {
            "type": "string",
            "minLength": 1,
            "description": "Database name",
        },
        DatabaseConfKey.TABLE_NAME: {
            "type": "string",
            "minLength": 1,
            "description": "Database table ",
        },
        DatabaseConfKey.TIMEZONE: {
            "type": "string",
            "minLength": 1,
            "description": "Predefined session timezone",
        },
        DatabaseConfKey.BATCH_SIZE: {
            "type": "integer",
            "minimum": 1,
            "description": "Database batch size: message are queued until batch size is reached",
        },
        DatabaseConfKey.WAIT_MAX_SECONDS: {
            "type": "integer",
            "minimum": 0,
            "description": "Wait (seconds) Queued messages are stored into database even the batch size is not reached.",
        },
        DatabaseConfKey.CLEAN_UP_AFTER_DAYS: {
            "type": "integer",
            "description": "Delete entries older than <n> days. Deactivate clean up with values values <= 0.",
        },
    },
    "additionalProperties": False,
    "required": [DatabaseConfKey.HOST, DatabaseConfKey.PORT, DatabaseConfKey.DATABASE],
}


class DatabaseException(Exception):
    pass


class Database(abc.ABC):
    DEFAULT_TABLE_NAME = "journal"

    def __init__(self, config):
        self._config = config
        self._last_connect_time: datetime.datetime | None = None
        self._pool: asyncpg.Pool | None = None
        self._table_name: str = config.get(
            DatabaseConfKey.TABLE_NAME, self.DEFAULT_TABLE_NAME
        )  # define by SQL scripts
        self._timezone: str | None = config.get(DatabaseConfKey.TIMEZONE)

    @property
    def is_connected(self) -> bool:
        if not self._pool:
            return False
        return not bool(self._pool.is_closing())

    async def connect(self):
        self._pool = await asyncpg.create_pool(**self._config)
        async with self._pool.acquire() as connection:
            time_zone = self._timezone or self.get_default_time_zone_name()
            query = f"set timezone='{time_zone}'"
            try:
                await connection.execute(query)
            except Exception:
                _logger.error(f"setting timezone failed ({query})!")
                raise

            self._last_connect_time = self._now()

    async def close(self):
        try:
            if self._pool:
                await self._pool.close()
        except Exception as ex:
            _logger.exception(ex)
        finally:
            self._pool = None

    @classmethod
    def get_default_time_zone_name(cls):
        local_timezone = get_localzone()
        if not local_timezone:
            local_timezone = (
                datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
            )
        return str(local_timezone)

    @classmethod
    def _now(cls) -> datetime:
        """overwritable `datetime.now` for testing"""
        return datetime.datetime.now(tz=get_localzone())
