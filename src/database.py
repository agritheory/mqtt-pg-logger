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


class Database(abc.ABC):
	DEFAULT_TABLE_NAME = "journal"

	def __init__(self, config: dict) -> None:
		self._config = config
		self._last_connect_time: datetime.datetime | None = None
		self._pool: asyncpg.Pool | None = None
		self._table_name: str = config.get(
			DatabaseConfKey.TABLE_NAME, self.DEFAULT_TABLE_NAME
		)  # define by SQL scripts
		self._timezone: str | None = config.get(DatabaseConfKey.TIMEZONE)

	@staticmethod
	def get_default_time_zone() -> str:
		return str(get_localzone())

	@staticmethod
	def _now() -> datetime.datetime:
		"""overwritable `datetime.now` for testing"""
		return datetime.datetime.now(tz=get_localzone())

	async def connect(self) -> None:
		self._pool = await asyncpg.create_pool(**self._config)
		await self.set_timezone()

	async def set_timezone(self) -> None:
		async with self._pool.acquire() as connection:
			time_zone = self._timezone or self.get_default_time_zone()
			query = f"set timezone='{time_zone}'"
			try:
				await connection.execute(query)
			except Exception:
				_logger.error(f"setting timezone failed ({query})!")
				raise

			self._last_connect_time = self._now()

	async def close(self) -> None:
		try:
			if self._pool:
				await self._pool.close()
		except Exception as ex:
			_logger.exception(ex)
		finally:
			self._pool = None
