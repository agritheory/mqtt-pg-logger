from src.database import DatabaseConfKey


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

LOGGING_CHOICES = ["debug", "info", "warning", "error"]
LOGGING_JSONSCHEMA = {
	"type": "object",
	"properties": {
		"log_file": {
			"type": "string",
			"minLength": 1,
			"description": "Log file (path)",
		},
		"log_level": {
			"type": "string",
			"enum": LOGGING_CHOICES,
			"description": "Log level",
		},
		"max_bytes": {
			"type": "integer",
			"minimum": 102400,
			"description": "Max bytes per log files.",
		},
		"max_count": {
			"type": "integer",
			"minimum": 1,
			"description": "Max count of rolled log files.",
		},
	},
}

CONFIG_JSONSCHEMA = {
	"type": "object",
	"properties": {
		"database": DATABASE_JSONSCHEMA,
		"logging": LOGGING_JSONSCHEMA,
		"mqtt": MQTT_JSONSCHEMA,
	},
	"additionalProperties": False,
	"required": ["database", "mqtt"],
}
