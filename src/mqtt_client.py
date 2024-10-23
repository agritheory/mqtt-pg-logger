import aiomqtt

from src.app_config import AppConfig
from src.constants import MqttConfKey


class MqttClient:

    DEFAULT_KEEPALIVE = 60
    DEFAULT_PORT = 1883
    DEFAULT_PORT_SSL = 8883
    DEFAULT_PROTOCOL = 5  # 5==MQTTv5, default: 4==MQTTv311, 3==MQTTv31
    DEFAULT_QUALITY = 1

    def __init__(self, config: AppConfig):

        self._client = None
        self._connection_error_info: str | None = None
        self._shutdown = False

        self._mqtt = config.get_mqtt_config()

        self._host = self._mqtt.get(MqttConfKey.HOST)
        self._port = self._mqtt.get(MqttConfKey.PORT)
        if not self._port:
            self._port = self.DEFAULT_PORT_SSL if is_ssl else self.DEFAULT_PORT
        self._user = self._mqtt.get(MqttConfKey.USER)
        self._password = self._mqtt.get(MqttConfKey.PASSWORD)
        self._keepalive = self._mqtt.get(MqttConfKey.KEEPALIVE, self.DEFAULT_KEEPALIVE)
        self._client_id = self._mqtt.get(MqttConfKey.CLIENT_ID)

        protocol = self._mqtt.get(MqttConfKey.PROTOCOL, self.DEFAULT_PROTOCOL)
        subscriptions = self._mqtt.get(MqttConfKey.SUBSCRIPTIONS)

        if not self._host or not subscriptions:
            raise ValueError(
                f"mandatory mqtt configuration not found ({MqttConfKey.HOST}, {MqttConfKey.SUBSCRIPTIONS})'!"
            )

        # SSL and TLS context
        ssl_ca_certs = self._mqtt.get(MqttConfKey.SSL_CA_CERTS)
        ssl_certfile = self._mqtt.get(MqttConfKey.SSL_CERTFILE)
        ssl_keyfile = self._mqtt.get(MqttConfKey.SSL_KEYFILE)
        ssl_insecure = self._mqtt.get(MqttConfKey.SSL_INSECURE, False)
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
            protocol=aiomqtt.ProtocolVersion(protocol),
            keepalive=self._keepalive,
            tls_insecure=is_ssl and ssl_insecure,
            tls_params=is_ssl and tls_params,
        )
