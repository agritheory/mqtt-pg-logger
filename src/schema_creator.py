import logging
import os

from asyncpg.exceptions import DuplicateObjectError, DuplicateTableError

from src.database import Database
from src.database_utils import DatabaseUtils

_logger = logging.getLogger(__name__)


class SchemaCreator(Database):
    def __init__(self, config) -> None:
        super().__init__(config)

    @staticmethod
    def get_script_path(script_name) -> str:
        file_path = os.path.dirname(__file__)
        project_dir = os.path.dirname(file_path)  # go up one time
        return os.path.join(project_dir, "sql", script_name)

    async def create_schema(self) -> None:
        if self._table_name != self.DEFAULT_TABLE_NAME:
            raise ValueError(
                f"Cannot create the database schema if an individual table name ({self._table_name}) is configured."
                "Use the default name ({self.DEFAULT_TABLE_NAME}) or adapt and execute the SQL scripts manually!"
            )

        # if table exists, an error is thrown anyway, so no need for check explicitly.

        script = self.get_script_path("table.sql")
        commands = DatabaseUtils.load_commands(script)
        await self._execute_commands(commands)
        _logger.info("table and indices created.")

        script = self.get_script_path("convert.sql")
        command = DatabaseUtils.load_as_single_command(script)
        await self._execute_commands([command])
        _logger.info("json convert function created.")

        script = self.get_script_path("trigger.sql")
        command = DatabaseUtils.load_as_single_command(script)
        await self._execute_commands([command])
        _logger.info("json convert trigger created.")

    async def _execute_commands(self, commands: list[str]) -> None:
        async with self._pool.acquire() as connection:
            for command in commands:
                try:
                    await connection.execute(command)
                except (
                    DuplicateObjectError,
                    DuplicateTableError,
                ) as ex:
                    # if table already exists, skip
                    _logger.info("db-command skipped: %s\n%s", ex, command)
                except Exception as ex:
                    _logger.error("db-command failed: %s\n%s", ex, command)
                    raise
