#!/usr/bin/env python3
import asyncio
import logging
import sys
from functools import wraps

import click

from src.app_config import AppConfig
from src.app_logging import LOGGING_CHOICES, AppLogging
from src.runner import Runner
from src.schema_creator import SchemaCreator

_logger = logging.getLogger(__name__)


def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.command()
@click.option(
    "--config-file",
    default="/etc/mqtt-pg-logger.yaml",
    help="Config file",
    show_default=True,
    type=click.Path(exists=True),
)
@click.option(
    "--create",
    is_flag=True,
    help="Create database table (if not exists) and create or replace a trigger",
)
@click.option("--log-file", help="Log file (if stated journal logging is disabled)")
@click.option(
    "--log-level",
    help="Log level",
    type=click.Choice(LOGGING_CHOICES, case_sensitive=False),
)
@click.option("--print-logs", is_flag=True, help="Print log output to console too")
@click.option(
    "--systemd-mode",
    is_flag=True,
    help="Systemd/journald integration: skip timestamp + prints to console",
)
@coro
async def _main(config_file, create, log_file, log_level, print_logs, systemd_mode):
    try:
        await run_service(
            config_file, create, log_file, log_level, print_logs, systemd_mode
        )

        # async with asyncio.TaskGroup() as tg:
        #     tg.create_task(
        #         run_service(
        #             config_file, create, log_file, log_level, print_logs, systemd_mode
        #         )
        #     )
    except KeyboardInterrupt:
        pass  # exits 0 by default
    except Exception as ex:
        _logger.exception(ex)
        sys.exit(1)  # a simple return is not understood by click


async def run_service(
    config_file, create, log_file, log_level, print_logs, systemd_mode
):
    """Logs MQTT messages to a Postgres database."""

    creator: SchemaCreator | None = None
    runner: Runner | None = None

    try:
        app_config = AppConfig(config_file)
        AppLogging.configure(
            app_config.get_logging_config(),
            log_file,
            log_level,
            print_logs,
            systemd_mode,
        )

        _logger.debug("start")

        if create:
            creator = SchemaCreator(app_config.get_database_config())
            await creator.connect()
            await creator.create_schema()
        else:
            runner = Runner(app_config)
            await runner.loop()
    finally:
        _logger.info("shutdown")

        if creator is not None:
            await creator.close()
        if runner is not None:
            await runner.close()


if __name__ == "__main__":
    asyncio.run(_main())
