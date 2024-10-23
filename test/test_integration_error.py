from test.setup_test import SetupTest
from test.utils import create_config_file

import pytest

from src.database import DatabaseConfKey
from src.mqtt_pg_logger import run_service


@pytest.fixture(scope="session")
def database_config():
    config_data = SetupTest.read_test_config()
    database_config = config_data["database"]
    database_config[DatabaseConfKey.HOST] = "host_should_not_exit"
    database_config[DatabaseConfKey.PORT] = 5435
    database_config[DatabaseConfKey.USER] = "no_matter"
    database_config[DatabaseConfKey.PASSWORD] = "no_matter"
    database_config[DatabaseConfKey.DATABASE] = "no_matter"
    database_config[DatabaseConfKey.TABLE_NAME] = "no_matter"
    return database_config


@pytest.fixture(scope="session")
def config_file(database_config):
    config_data = SetupTest.read_test_config()
    return create_config_file(config_data, database_config, ["#"])


async def test_no_database_abort(config_file):
    with pytest.raises(TypeError) as ex:
        await run_service(config_file, False, None, "info", True, True)
        assert "connect() got an unexpected keyword argument" in str(ex)
