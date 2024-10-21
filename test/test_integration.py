import copy
from pathlib import Path
import threading
import time

import pytest
import pytest_asyncio
from test.mocked_lifecycle_control import MockedLifecycleControl
from test.mqtt_publisher import MqttPublisher
from test.setup_test import SetupTest
from unittest import mock

import attr
from pytest_postgresql import factories

from src.database import DatabaseConfKey
from src.lifecycle_control import LifecycleControl
from src.mqtt_listener import MqttConfKey
from src.mqtt_pg_logger import run_service
from test.utils import create_config_file


@attr.s
class Subscription:
    topic: str = attr.ib()
    subscription: str | None = attr.ib()
    skip: bool = attr.ib()


@attr.s
class SentMessage:
    subscription: Subscription = attr.ib()
    text: str = attr.ib()
    message_id: int = attr.ib(default=None)


postgresql_external = factories.postgresql_noproc(
    user="postgres",
    password="postgres",
    dbname="postgres-test",
    # load=[Path.cwd() / "test" / "sql" / "table.sql"],
)
postgresql = factories.postgresql("postgresql_external")


@pytest.fixture
def subscriptions():
    test_config_data = SetupTest.read_test_config()
    sub_base = test_config_data["mqtt"][MqttConfKey.TEST_SUBSCRIPTION_BASE]

    return [
        Subscription(
            topic=sub_base + "/inside/log1",
            subscription=sub_base + "/inside/#",
            skip=False,
        ),
        Subscription(
            topic=sub_base + "/inside/log2",
            subscription=sub_base + "/inside/#",
            skip=False,
        ),
        Subscription(topic=sub_base + "/outside", subscription=None, skip=True),
    ]


@pytest.fixture
def topics(subscriptions):
    return [s.subscription for s in subscriptions if s.subscription]


@pytest.fixture
def pg_config(postgresql):
    return {
        DatabaseConfKey.USER: postgresql.info.user,
        DatabaseConfKey.HOST: postgresql.info.host,
        DatabaseConfKey.PORT: postgresql.info.port,
        DatabaseConfKey.DATABASE: postgresql.info.dbname,
        DatabaseConfKey.PASSWORD: postgresql.info.password,
    }


@pytest.fixture
def config_file(pg_config, topics):
    test_config_data = SetupTest.read_test_config()
    config_file = create_config_file(test_config_data, pg_config, topics)
    return config_file


@pytest_asyncio.fixture
async def runner(config_file):
    await run_service(config_file, True, None, "info", True, True)  # create schema
    await run_service(config_file, False, None, "info", True, True)  # run loop


@pytest.fixture
def setup_data():
    service_thread = None

    test_config_data = SetupTest.read_test_config()
    publisher_config_data = copy.deepcopy(test_config_data["mqtt"])
    publisher_config_data[MqttConfKey.CLIENT_ID] = MqttPublisher.get_default_client_id()
    mqtt_publisher = MqttPublisher(publisher_config_data)
    mqtt_publisher.connect()

    yield mqtt_publisher

    mqtt_publisher.close()
    if service_thread:
        try:
            while service_thread.is_alive():
                service_thread.join(
                    1
                )  # join shortly to not block KeyboardInterrupt exceptions
        except KeyboardInterrupt:
            pass

    SetupTest.close_database()


class TestIntegration:

    def run_service_threaded(self):
        kwargs = {
            "config_file": self._config_file,
            "create": False,
            "log_file": None,
            "log_level": "info",
            "print_logs": True,
            "systemd_mode": True,
        }

        async def run_service_locally():
            await run_service(**kwargs)

        service_thread = threading.Thread(target=run_service_locally, daemon=True)
        service_thread.start()

    @mock.patch.object(
        LifecycleControl, "get_instance", MockedLifecycleControl.get_instance
    )
    def test_full_integration(self, setup_data, subscriptions, runner, postgresql):
        # self.run_service_threaded()

        mqtt_publisher = setup_data

        sent_messages = []
        unique_id = 0
        for _ in range(1, 9):
            for subscription in subscriptions:
                unique_id = unique_id + 1
                message = SentMessage(
                    subscription=subscription, text=f"{unique_id}-{subscription.topic}"
                )
                result = mqtt_publisher.publish(
                    topic=message.subscription.topic, payload=message.text
                )
                message.message_id = result.mid

                if not message.subscription.skip:
                    sent_messages.append(message)

        time.sleep(3)
        mqtt_publisher.close()

        result = postgresql.execute("select * from journal").fetchall()
        print(result)

        fetched_rows = SetupTest.query_all("select * from journal")
        assert len(fetched_rows) == len(sent_messages)
        fetched_messages = {row["text"]: row for row in fetched_rows}

        for sent_message in sent_messages:
            fetched_message = fetched_messages[sent_message.text]
            assert fetched_message["topic"] == sent_message.subscription.topic
