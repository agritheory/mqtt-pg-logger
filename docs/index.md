# MQTT Logger

Depends on Apache ActiveMQ Artemis and TimescaleDB

Exposes a GraphQL API for configuration

Running `docker compose up --build` will start three services:
 - A TimescaleDB container
 - An Artemis Active MQ container
 - A python container running Quart

## Configuration options:

### Quart configuration

```env
# Quart
SECRET_KEY=yohjohthieNguvayaiFaeph5Oomae9nu
FERNET_KEY=D7jNgKGahOrZtQVd9reaT53B4SAz-gLH2ZJtRStpCsY=
JWT_SECRET_KEY=59eggsXI2uU2eaWmmOcr_zMKfhE4rW-h0avo0IKNJS4
HOST=0.0.0.0
PORT=5000
ACCESS_TOKEN_EXPIRES=3600
REFRESH_TOKEN_EXPIRES=2592000
CORS_ORIGINS=["*"]
```

### TimescaleDB Configuration options
```env
# Database Configuration
DB_HOST=timescaledb
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=postgres
```

### Admin account user configuration
```env
# Admin User Configuration
ADMIN_EMAIL=admin@agritheory.dev
ADMIN_PASSWORD=ohch4GeiSie
```

### MQTT Broker (Active MQ Artemis) configuration
```env
#MQTT Broker config
MQTT_BROKER_HOST=artemis
MQTT_BROKER_PORT=1883
MQTT_BROKER_WEB_CONSOLE_PORT=8161
MQTT_USER=artemis
MQTT_PASSWORD=artemis
SSL_INSECURE=True
MQTT_CLIENT_ID=mqtt-logger
MQTT_KEEPALIVE=60
MQTT_DEFAULT_PROTOCOL=5  # 5==MQTTv5, default: 4==MQTTv311, 3==MQTTv31
MQTT_DEFAULT_QUALITY=1
```

### Production configuration
```env
# Caddy
DOMAIN=''

# SSL config for Artemis
SSL_CA_CERTS=''
SSL_CERTFILE=''
SSL_KEYFILE=''
```

## Using the GraphQL API

You can expose the GraphiQL interface, which in development will be located at http:localhost:5000/graphql/. The default admin username and password are set in the `.env` file; reusing this username and password is fine for testing and development but should not be used in production.

```graphql
mutation Login($username: String!, $password: String!) {
  login(input: {
    username: $username,
    password: $password
  }) {
    message
    accessToken
    refreshToken
    tokenType
    expiresIn
  }
}
```
You can then copy that JWT token to create a header like:

```json
{
  "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbkBhZ3JpdGhlb3J5LmRldiIsImV4cCI6MTc0MzI2Mjg3OSwiaWF0IjoxNzQzMjU5Mjc5LCJqdGkiOiJESkcwWnRldWJLd0ljUTh4SEE4R1ZBIn0.xYf-HL1ipYmToS0-LIGHk5wWKqHu7y93dNt5LynVVk8"
}
```
And query like:

```graphql
query HealthCheck{
  health {
    status
    timestamp
    mqttConnection
    timescaledbStatus
    artemisStatus
  }
}
```


