#!/bin/sh

[ -n "${MQTT_HOST}" ] || exit 1

cd /mqtt-pg-logger
cp mqtt-pg-logger.yaml.sample mqtt-pg-logger.yaml
chmod 600 mqtt-pg-logger.yaml
sed -i -e "s#<your[_ ]broker>#${MQTT_HOST}#g" \
    -e "s#<your[_ ]database[_ ]password>#${POSTGRES_PASSWORD}#g" \
    -e "s#<your[_ ]database[_ ]user>#${POSTGRES_USER}#g" \
    -e "s#<your[_ ]database[_ ]name>#${POSTGRES_DATABASE}#g" \
    -e "s#<your[_ ]database[_ ]host>#${POSTGRES_HOST}#g" \
    -e "s#<your_mqtt_user>#${MQTT_USER}#g" \
    -e "s#<your_mqtt_password>#${MQTT_PASSWORD}#g" \
    -e "s#<path_to_your_ca_cert>#${CA_CERT_PATH}#g" \
    mqtt-pg-logger.yaml

cat mqtt-pg-logger.yaml

sh ./mqtt-pg-logger.sh --print-logs --config-file ./mqtt-pg-logger.yaml