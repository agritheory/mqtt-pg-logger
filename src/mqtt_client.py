import datetime
import threading

import aiomqtt
from tzlocal import get_localzone


class MqttConfKey:
    CLIENT_ID = "client_id"
    HOST = "host"
    PORT = "port"
    PASSWORD = "password"
    USER = "user"
    KEEPALIVE = "keepalive"
    PROTOCOL = "protocol"

    SSL_CA_CERTS = "ssl_ca_certs"
    SSL_CERTFILE = "ssl_certfile"
    SSL_INSECURE = "ssl_insecure"
    SSL_KEYFILE = "ssl_keyfile"

    SUBSCRIPTIONS = "subscriptions"
    SKIP_SUBSCRIPTION_REGEXES = "skip_subscription_regexes"

    TEST_SUBSCRIPTION_BASE = "test_subscription_base"  # Test only


SUBSCRIPTION_JSONSCHEMA = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
}


SKIP_SUBSCRIPTION_JSONSCHEMA = {
    "type": "array",
    "items": {
        "type": "string",
        "minLength": 1,
        "description": "If this regex matches the topic the message is skipped.",
        # "pattern": "[A-Za-z\/]*",  # no "#" at end
    },
}


MQTT_JSONSCHEMA = {
    "type": "object",
    "properties": {
        MqttConfKey.CLIENT_ID: {"type": "string", "minLength": 1},
        MqttConfKey.HOST: {"type": "string", "minLength": 1},
        MqttConfKey.KEEPALIVE: {"type": "integer", "minimum": 1},
        MqttConfKey.PORT: {"type": "integer"},
        MqttConfKey.PROTOCOL: {"type": "integer", "enum": [3, 4, 5]},
        MqttConfKey.SSL_CA_CERTS: {"type": "string", "minLength": 1},
        MqttConfKey.SSL_CERTFILE: {"type": "string", "minLength": 1},
        MqttConfKey.SSL_INSECURE: {"type": "boolean"},
        MqttConfKey.SSL_KEYFILE: {"type": "string", "minLength": 1},
        MqttConfKey.USER: {"type": "string", "minLength": 1},
        MqttConfKey.PASSWORD: {"type": "string"},
        MqttConfKey.SUBSCRIPTIONS: SUBSCRIPTION_JSONSCHEMA,
        MqttConfKey.SKIP_SUBSCRIPTION_REGEXES: SKIP_SUBSCRIPTION_JSONSCHEMA,
        MqttConfKey.TEST_SUBSCRIPTION_BASE: {
            "type": "string",
            "minLength": 1,
            "description": "For test only: base topic (get extended)",
            # "pattern": "[A-Za-z\/]*",  # no "#" at end
        },
    },
    "additionalProperties": False,
    "required": [MqttConfKey.HOST, MqttConfKey.PORT, MqttConfKey.SUBSCRIPTIONS],
}


class MqttException(Exception):
    pass


class MqttClient:

    DEFAULT_KEEPALIVE = 60
    DEFAULT_PORT = 1883
    DEFAULT_PORT_SSL = 8883
    DEFAULT_PROTOCOL = 5  # 5==MQTTv5, default: 4==MQTTv311, 3==MQTTv31
    DEFAULT_QUALITY = 1

    def __init__(self, config):

        self._client = None
        self._connection_error_info: str | None = None
        self._shutdown = False

        self._lock = threading.Lock()

        self._host = config[MqttConfKey.HOST]
        self._port = config.get(MqttConfKey.PORT)
        if not self._port:
            self._port = self.DEFAULT_PORT_SSL if is_ssl else self.DEFAULT_PORT
        self._user = config.get(MqttConfKey.USER)
        self._password = config.get(MqttConfKey.PASSWORD)
        self._keepalive = config.get(MqttConfKey.KEEPALIVE, self.DEFAULT_KEEPALIVE)
        self._client_id = config.get(MqttConfKey.CLIENT_ID)

        protocol = config.get(MqttConfKey.PROTOCOL, self.DEFAULT_PROTOCOL)
        subscriptions = config.get(MqttConfKey.SUBSCRIPTIONS)

        if not self._host or not subscriptions:
            raise ValueError(
                f"mandatory mqtt configuration not found ({MqttConfKey.HOST}, {MqttConfKey.SUBSCRIPTIONS})'!"
            )

        # SSL and TLS context
        ssl_ca_certs = config.get(MqttConfKey.SSL_CA_CERTS)
        ssl_certfile = config.get(MqttConfKey.SSL_CERTFILE)
        ssl_keyfile = config.get(MqttConfKey.SSL_KEYFILE)
        ssl_insecure = config.get(MqttConfKey.SSL_INSECURE, False)
        is_ssl = ssl_ca_certs or ssl_certfile or ssl_keyfile

        tls_params = {
            "ca_certs": ssl_ca_certs,
            "certfile": ssl_certfile,
            "keyfile": ssl_keyfile,
        }

        self._client = aiomqtt.Client(
            hostname=self._host,
            port=self._port,
            username=self._user,
            password=self._password,
            identifier=self._client_id,
            protocol=aiomqtt.ProtocolVersion.V5,
            keepalive=self._keepalive,
            tls_insecure=is_ssl and ssl_insecure,
            tls_params=is_ssl and tls_params,
        )

    @classmethod
    def _now(cls) -> datetime.datetime:
        return datetime.datetime.now(tz=get_localzone())
