#!/bin/sh

[ -n "${MQTT_HOST}" ] || exit 1

cd /mqtt-pg-logger
cp mqtt-pg-logger.yaml.sample mqtt-pg-logger.yaml
chmod 600 mqtt-pg-logger.yaml
sed -i -e "s#<broker>#${MQTT_HOST}#g" \
    -e "s#<database_password>#${DATABASE_PASSWORD}#g" \
    -e "s#<database_user>#${DATABASE_USER}#g" \
    -e "s#<database_name>#${DATABASE_NAME}#g" \
    -e "s#<database_host>#${DATABASE_HOST}#g" \
    -e "s#<database_port>#${DATABASE_PORT}#g" \
    -e "s#<mqtt_host>#${MQTT_HOST}#g" \
    -e "s#<mqtt_user>#${MQTT_USER}#g" \
    -e "s#<mqtt_password>#${MQTT_PASSWORD}#g" \
    -e "s#<mqtt_port>#${MQTT_PORT}#g" \
    -e "s#<ssl_insecure>#${SSL_INSECURE}#g" \
    mqtt-pg-logger.yaml

cat mqtt-pg-logger.yaml

PYTHONTRACEMALLOC=1 sh ./mqtt-pg-logger.sh --create --print-logs --config-file ./mqtt-pg-logger.yaml
PYTHONTRACEMALLOC=1 sh ./mqtt-pg-logger.sh --print-logs --config-file ./mqtt-pg-logger.yaml