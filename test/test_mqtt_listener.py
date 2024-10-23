import unittest
from test.setup_test import SetupTest
from unittest import mock

from src.constants import MqttConfKey
from src.mqtt_listener import MqttListener


class MqttException(Exception):
    pass


class TestMqttListener(unittest.TestCase):

    @classmethod
    def create_listener(cls, skip_subscriptions):
        test_config_data = SetupTest.read_test_config()
        test_config_mqtt = test_config_data["mqtt"]
        test_config_mqtt[MqttConfKey.SUBSCRIPTIONS] = ["base1/#", "base2/#"]
        test_config_mqtt[MqttConfKey.SKIP_SUBSCRIPTION_REGEXES] = skip_subscriptions

        return MqttListener(test_config_mqtt)

    def test_accept_topic(self):
        listener = self.create_listener(["base1/exclude", "^base2/exclude"])

        self.assertTrue(listener._accept_topic("base1/include"))
        self.assertTrue(listener._accept_topic("base1/include/exclude"))

        self.assertFalse(listener._accept_topic("base1/exclude"))
        self.assertFalse(listener._accept_topic("base1/exclude2"))
        self.assertFalse(listener._accept_topic("base1/exclude/2"))

        self.assertFalse(listener._accept_topic("base2/exclude"))
        self.assertFalse(listener._accept_topic("base2/exclude2"))
        self.assertFalse(listener._accept_topic("base2/exclude/2"))

        self.assertTrue(listener._accept_topic("base1/include/base2/exclude"))


class TestMqttListenerConnectionErrors(unittest.TestCase):

    @classmethod
    def create_listener(cls):
        test_config_data = SetupTest.read_test_config()
        test_config_mqtt = test_config_data["mqtt"]
        test_config_mqtt[MqttConfKey.SUBSCRIPTIONS] = ["base/#"]
        test_config_mqtt[MqttConfKey.SKIP_SUBSCRIPTION_REGEXES] = []

        listener = MqttListener(test_config_mqtt)

        sleep_counter = 0

        def sleep(seconds):
            nonlocal sleep_counter
            sleep_counter += 1

            if sleep_counter == 3:
                listener._on_connect(None, None, None, 0, None)

            return seconds

        return listener

    @mock.patch("paho.mqtt.client.Client")
    def test_subscribe_success(self, _):
        listener = self.create_listener()
        listener._client.subscribe.return_value = (0, "dummy")

        listener.connect()
        self.assertTrue(listener.is_connected)

    @mock.patch("paho.mqtt.client.Client")
    def test_subscribe_unexpected_disconnect(self, _mock_mqtt_client):
        listener = self.create_listener()
        listener._client.subscribe.return_value = (0, "dummy")

        listener.connect()
        self.assertTrue(listener.is_connected)

        listener._on_disconnect(None, None, 7)
        self.assertFalse(listener.is_connected)

        with self.assertRaises(MqttException):
            listener.ensure_connection()

    @mock.patch("paho.mqtt.client.Client")
    def test_subscribe_failure(self, _mock_mqtt_client):
        listener = self.create_listener()
        listener._client.subscribe.return_value = (7, "dummy")

        with self.assertRaises(MqttException):
            listener.connect()

        self.assertFalse(listener.is_connected)

    @mock.patch("paho.mqtt.client.Client")
    def test_subscribe_failure2(self, _mock_mqtt_client):
        listener = self.create_listener()
        listener._client.subscribe.return_value = (7, "dummy")

        with self.assertRaises(MqttException):
            listener.connect()

        self.assertFalse(listener.is_connected)
