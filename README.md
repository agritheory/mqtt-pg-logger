# mqtt-pg-logger

Collects [MQTT](https://en.wikipedia.org/wiki/MQTT) messages and stores them in a Postgres database.

- Runs as Linux service.
- Provides the message payload as standard VARCHAR text and additionally converts the payload into a JSONB column if compatible. (See: [trigger.sql](./sql/trigger.sql) and [convert.sql](./sql/convert.sql))
- Clean up old messages (after x days).

## Docker

### Quickstart

```docker compose up```

### Configuration
The docker compose file is setup to run
- mqtt-pg-logger
- ActiveMQ Artemis
- TimescaleDB

All configuration is done via env vars in the docker compose file, and default configuration is setup to just work out of the box.

**Ports**
- `8161` - Artemis Web UI
- `616161` - Artemis main messaging port, accepts all protocols
- `1883` - Artemis MQTT port

### Development
When developing mqtt-pg-logger code make sure to rebuild the docker image after making code changes if everything is running in docker.

```
docker compose build mqtt-pg-logger
docker compose up --watch
```

## Setting up TLS

### 1. Generating certificates 
This specific example is meant for Let's Encrypt certificates. If you already have obtained certificates skip to he next step.

1. Install [certbot](https://certbot.eff.org/)
2. We want to obtain a certificate but not "install" it so we must run `certbot` in `certonly` mode 
3. Additionally we will need to use either the [standalone](https://eff-certbot.readthedocs.io/en/stable/using.html#standalone) or a [DNS Plugin](https://eff-certbot.readthedocs.io/en/stable/using.html#dns-plugins) to authenticate. 

Example - 
```
sudo certbot certonly --standalone -d [domain.name] 
```

### 2. Making certificates compatible for ActiveMQ Artemis
Artemis, being Java based, doesn't directly work with `.pem` files. We need to create a Java Keystore file (`.jks`) and load up our certificates into that file. 

1. Install the Java JRE, we need this for the `keytool` CLI utility that we'll use to create a keystore
2. We need to convert our `.pem` files into the PKCS12 format. This will ask for an "Export Password", for this we can use a temporary password that we will use in the next step.

```
sudo openssl pkcs12 -export -out fullchain.p12 -in /etc/letsencrypt/live/[domain.name]/fullchain.pem -inkey /etc/letsencrypt/live/[domain.name]/privkey.pem -name "le_cert"
```

3. Load the PKCS12 file into a new Java keystore
The password you use to create the keystore will be used to configure Artemis in the next step, so make sure to store it safely. [export_password] is the password we set in the previous step.
```
sudo keytool -importkeystore -deststorepass [password] -destkeypass [password] -destkeystore le_keystore.jks -srckeystore fullchain.p12 -srcstoretype PKCS12 -srcstorepass [export_password]
```
 The `fullchain.p12` file can be now safely deleted. 

### 3. Configuring ActiveMQ Artemis

1. Create a new folder called `certs` and move your Java keystore file into it (`le_keystore.jks` if you have followed the previous steps)
2. Create a new `broker.xml` file using the `broker.xml.sample` and update the `acceptor` values with the right certificate path and password
3. Update the `docker-compose.prod.yml` file with the right certificate path and password

### Renewing certificates 

1. Renew certificates
You can renew certificates normally by running `certbot renew`
2. Remove old certificate from the keystore 
```
sudo keytool -delete -alias le_cert -keystore ./le_keystore.jks
```
3. Convert new certificate to pkcs12 - Same as above
```
sudo openssl pkcs12 -export -out fullchain.p12 -in /etc/letsencrypt/live/[domain.name]/fullchain.pem -inkey /etc/letsencrypt/live/[domain.name]/privkey.pem -name "le_cert"
```
4. Load the certificate to keystore - Same as above
```
sudo keytool -importkeystore -deststorepass [password] -destkeypass [password] -destkeystore le_keystore.jks -srckeystore fullchain.p12 -srcstoretype PKCS12 -srcstorepass [export_password]
```

## Manual

### Prerequisites

Python 3 ...

```bash
sudo apt-get install python3-dev python3-pip python3-venv python3-wheel -y
```

### Prepare python environment

```bash
cd /opt
sudo mkdir mqtt-pg-logger
sudo chown <user>:<user> mqtt-pg-logger  # type in your user
git clone https://github.com/rosenloecher-it/mqtt-pg-logger mqtt-pg-logger

cd mqtt-pg-logger
python3 -m venv venv

# activate venv
source ./venv/bin/activate

# check python version >= 3.12
python --version

# install required packages
poetry install
```

### Configuration

```bash
# cd ... goto project dir
cp ./mqtt-pg-logger.yaml.sample ./mqtt-pg-logger.yaml

# security concerns: make sure, no one can read the stored passwords
chmod 600 ./mqtt-pg-logger.yaml
```

Edit your `mqtt-pg-logger.yaml`. See comments there.

### Run

```bash
# see command line options
./mqtt-pg-logger.sh --help

# prepare your own config file based on ./mqtt-pg-logger.yaml.sample

# create database schema manually analog to ./scripts/*.sql or let the app do it
./mqtt-pg-logger.sh --create --print-logs --config-file ./mqtt-pg-logger.yaml

# start the logger
./mqtt-pg-logger.sh --print-logs --config-file ./mqtt-pg-logger.yaml
# abort with ctrl+c

```

## Register as systemd service
```bash
# prepare your own service script based on mqtt-pg-logger.service.sample
cp ./mqtt-pg-logger.service.sample ./mqtt-pg-logger.service

# edit/adapt paths and user in mqtt-pg-logger.service
vi ./mqtt-pg-logger.service

# install service
sudo cp ./mqtt-pg-logger.service /etc/systemd/system/
# alternative: sudo cp ./mqtt-pg-logger.service.sample /etc/systemd/system//mqtt-pg-logger.service
# after changes
sudo systemctl daemon-reload

# start service
sudo systemctl start mqtt-pg-logger

# check logs
journalctl -u mqtt-pg-logger
journalctl -u mqtt-pg-logger --no-pager --since "5 minutes ago"

# enable autostart at boot time
sudo systemctl enable mqtt-pg-logger.service
```

## Testing

### Prerequisites

- Docker
- [Act](https://nektosact.com/installation/index.html) (For running GitHub Actions locally).
  - I recommend installing the [GitHub CLI Extension](https://nektosact.com/installation/gh.html)

### How to run
Just run `act push` . If you're using the GitHub CLI Extension run `gh act push`. This will trigger the workflow file that should run on a GitHub `push` event. On first run, you will be asked to pick the size of the running, just pick 'Medium'.

## Additional infos

## Database infos

Consider running a `VACUUM ANALYZE` on your Postgres database on a periodic base (CRON).
This will [reclaim storage occupied by dead tuples](https://postgrespro.com/docs/postgresql/13/sql-vacuum).

### MQTT broker related infos

If no messages get logged check your broker.
```bash
sudo apt-get install mosquitto-clients

# prepare credentials
SERVER="<your server>"

# start listener
mosquitto_sub -h $SERVER -d -t smarthome/#

# send single message
mosquitto_pub -h $SERVER -d -t smarthome/test -m "test_$(date)"

# just as info: clear retained messages
mosquitto_pub -h $SERVER -d -t smarthome/test -n -r -d
```

Not that retained messages get logged again after a restart of the service.
And especially stale messages of meanwhile unused topic gets logged again and again.
```bash
SERVER="<your server>"
BASE_TOPIC="test/#"  # or "#"

# clear all retained topics
mosquitto_sub -h "$SERVER" -t "$BASE_TOPIC" -F "%t" --retained-only | while read line; do mosquitto_pub -h "$SERVER" -t "${line% *}" -r -n; done

# or clear a single topic
mosquitto_pub -h "$SERVER" -t the/topic -n -r -d
```

## Maintainer & License

MIT © [Raul Rosenlöcher](https://github.com/rosenloecher-it)

The code is available at [GitHub][home].

[home]: https://github.com/rosenloecher-it/mqtt-pg-logger

