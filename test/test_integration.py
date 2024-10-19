import copy
import os
import threading
import time
import unittest
from test.mocked_lifecycle_control import MockedLifecycleControl
from test.mqtt_publisher import MqttPublisher
from test.setup_test import SetupTest
from unittest import mock

import attr
import yaml

from src.database import DatabaseConfKey
from src.lifecycle_control import LifecycleControl, StatusNotification
from src.mqtt_listener import MqttConfKey
from src.mqtt_pg_logger import run_service


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


class BaseTestIntegration(unittest.TestCase):

    @classmethod
    def create_config_file(cls, test_config_data, database_config, topics):
        mqtt_config = copy.deepcopy(test_config_data["mqtt"])
        mqtt_config[MqttConfKey.SUBSCRIPTIONS] = topics

        data = {
            "database": database_config,
            "mqtt": mqtt_config,
        }

        config_file = SetupTest.get_test_path("config_file.yaml")
        with open(config_file, "w") as write_file:
            yaml.dump(data, write_file, default_flow_style=False)

        os.chmod(config_file, 0o600)

        return config_file


class TestIntegration(BaseTestIntegration):

    def setUp(self):
        SetupTest.init_database(skip_schema_creation=True)
        # SetupTest.init_logging()

        test_config_data = SetupTest.read_test_config()
        sub_base = test_config_data["mqtt"][MqttConfKey.TEST_SUBSCRIPTION_BASE]

        self.subscriptions = [
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
        topics = [s.subscription for s in self.subscriptions if s.subscription]

        database_config = copy.deepcopy(SetupTest.get_database_params())
        database_config[DatabaseConfKey.WAIT_MAX_SECONDS] = 1

        self._config_file = self.create_config_file(
            test_config_data, database_config, topics
        )
        run_service(self._config_file, True, None, "info", True, True)  # create schema

        # expected no error, table was created
        fetched = SetupTest.query_one("select count(1) from journal")
        self.assertEqual(fetched["count"], 0)

        self.service_thread = None

        publisher_config_data = copy.deepcopy(test_config_data["mqtt"])
        publisher_config_data[MqttConfKey.CLIENT_ID] = (
            MqttPublisher.get_default_client_id()
        )
        self.mqtt_publisher = MqttPublisher(publisher_config_data)
        self.mqtt_publisher.connect()

        self.mocked_lifecycle = MockedLifecycleControl.get_instance()
        self.mocked_lifecycle.reset()

    def tearDown(self):
        self.mqtt_publisher.close()

        if self.service_thread:
            self.mocked_lifecycle.shutdown()

            try:
                while self.service_thread.is_alive():
                    self.service_thread.join(
                        1
                    )  # join shortly to not block KeyboardInterrupt exceptions
            except KeyboardInterrupt:
                pass

        SetupTest.close_database()

    def run_service_threaded(self):
        kwargs = {
            "config_file": self._config_file,
            "create": False,
            "log_file": None,
            "log_level": "info",
            "print_logs": True,
            "systemd_mode": True,
        }

        def run_service_locally():
            try:
                run_service(**kwargs)
            except Exception as ex:
                self.mocked_lifecycle.set_exception(
                    ex
                )  # exception is invisible otherwise!
                raise

        self.service_thread = threading.Thread(target=run_service_locally, daemon=True)
        self.service_thread.start()

    @mock.patch.object(
        LifecycleControl, "get_instance", MockedLifecycleControl.get_instance
    )
    def test_full_integration(self):
        self.run_service_threaded()

        notifications = [
            StatusNotification.MESSAGE_STORE_CONNECTED,
            StatusNotification.MQTT_LISTENER_CONNECTED,
            StatusNotification.MQTT_LISTENER_SUBSCRIBED,
            StatusNotification.MQTT_PUBLISHER_CONNECTED,
        ]
        self.mocked_lifecycle.wait_for_notifications(notifications, 5, "run up")

        sent_messages = []
        unique_id = 0
        for i in range(1, 9):
            for subscription in self.subscriptions:
                unique_id = unique_id + 1
                message = SentMessage(
                    subscription=subscription, text=f"{unique_id}-{subscription.topic}"
                )
                result = self.mqtt_publisher.publish(
                    topic=message.subscription.topic, payload=message.text
                )
                message.message_id = result.mid

                if not message.subscription.skip:
                    sent_messages.append(message)

        notifications = [
            StatusNotification.RUNNER_QUEUE_EMPTIED,
            StatusNotification.MESSAGE_STORE_STORED,
        ]
        self.mocked_lifecycle.wait_for_notifications(
            notifications, 5, "wait for queue emptied"
        )
        time.sleep(3)  # 1s via DatabaseConfKey.WAIT_MAX_SECONDS

        self.mqtt_publisher.close()
        self.mocked_lifecycle.shutdown()
        notifications = [StatusNotification.MESSAGE_STORE_CLOSED]
        self.mocked_lifecycle.wait_for_notifications(notifications, 5, "shutdown")

        fetched_rows = SetupTest.query_all("select * from journal")
        self.assertEqual(len(fetched_rows), len(sent_messages))
        fetched_messages = {row["text"]: row for row in fetched_rows}

        for sent_message in sent_messages:
            fetched_message = fetched_messages[sent_message.text]
            self.assertEqual(fetched_message["topic"], sent_message.subscription.topic)


class TestIntegrationErrorNoDatabase(BaseTestIntegration):

    def setUp(self):
        # SetupTest.init_logging()

        config_data = SetupTest.read_test_config()

        database_config = config_data["database"]
        database_config[DatabaseConfKey.HOST] = "host_should_not_exit"
        database_config[DatabaseConfKey.PORT] = 5435
        database_config[DatabaseConfKey.USER] = "no_matter"
        database_config[DatabaseConfKey.PASSWORD] = "no_matter"
        database_config[DatabaseConfKey.DATABASE] = "no_matter"
        database_config[DatabaseConfKey.TABLE_NAME] = "no_matter"

        self._config_file = self.create_config_file(config_data, database_config, ["#"])

        self.service_thread = None

        self.mocked_lifecycle = MockedLifecycleControl.get_instance()
        self.mocked_lifecycle.reset()

    def tearDown(self):

        if self.service_thread:
            self.mocked_lifecycle.shutdown()

            try:
                while self.service_thread.is_alive():
                    self.service_thread.join(
                        1
                    )  # join shortly to not block KeyboardInterrupt exceptions
            except KeyboardInterrupt:
                pass

    @mock.patch.object(
        LifecycleControl, "get_instance", MockedLifecycleControl.get_instance
    )
    def test_no_database_abort(self):
        with self.assertRaises(RuntimeError) as ex:
            run_service(self._config_file, False, None, "info", True, True)

        self.assertTrue("database thread was finished" in str(ex.exception))
