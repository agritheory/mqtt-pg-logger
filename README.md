# mqtt-pg-logger

Collects [MQTT](https://en.wikipedia.org/wiki/MQTT) messages and stores them in a Postgres database.

- Runs as Linux service.
- Provides the message payload as standard VARCHAR text and additionally converts the payload into a JSONB column if compatible. (See: [trigger.sql](./sql/trigger.sql) and [convert.sql](./sql/convert.sql))
- Clean up old messages (after x days).

## Docker

### Development Quick-start

```docker compose up```

### Configuration
The docker compose file is setup to run
- mqtt-pg-logger
- ActiveMQ Artemis
- TimescaleDB

All configuration is done via env vars in the docker compose file, and default configuration is setup to just work out of the box. By default ActiveMQ Artemis and TimescaleDB are not setup for data persistence so when the containers are shut down the data will be lost. 

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
2. Create a new `broker.xml` file using the `broker.xml.sample` inside the `etc-override` folder and update the `acceptor` values with the right certificate path and password
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

## Infrastructure Overview

This an overview of how the infrastructure around mqtt-pg-logger works and how it is configured 

![image](https://github.com/user-attachments/assets/91598a2f-9d95-4e59-bbaf-d03d62134d58)

### ActiveMQ Artemis
ActiveMQ Artemis is a high performance and scalable message broker. It works with many protocols, but we only care about MQTT in our case. This is the bridge between our SCADA system and the rest of our software infra. 

#### Configuration
Note - For running ActiveMQ Artemis for development, no configuration is needed, everything should work out of the box when you spin up the `docker-compose.yml` file. For production there is a separate compose file named `docker-compose.prod.yml` which contains additional configuration which is explained below.

[Official Docs for Docker](https://activemq.apache.org/components/artemis/documentation/latest/docker.html)

Configuration for ActiveMQ Artemis is stored in the `broker.xml` file that is mounted into the Docker container at runtime. The prebuilt image allows us to drop a file in a specific directory that then gets copied over when the container is spun up. The only keys that we care about the in the `broker.xml` are the `<acceptor>` keys. The sample `broker.xml.sample` file is modified in a way that TLS configuration can be easily set. Below is the MQTT acceptor configuration that's been updated to use SSL. Only the `[keystore.jks]` and the `[password]` values need to be updated. Detailed information on how to enable SSL can be found in the "Setting up TLS" section above. 

```
<acceptor name="mqtt">tcp://0.0.0.0:8883?sslEnabled=true;keyStorePath=/etc/ssl/certs/[keystore.jks];keyStorePassword=[password];tcpSendBufferSize=1048576;tcpReceiveBufferSize=1048576;protocols=MQTT;useEpoll=true</acceptor>
```

Certificates generated for TLS are mounted to the `/etc/ssl/certs` directory and the data for Artemis is stored in a Docker volume. 

```
    volumes:
    - ./certs:/etc/ssl/certs
    - ./etc-override:/var/lib/artemis-instance/etc-override # broker.xml override
    -  artemis-data:/var/lib/artemis-instance/data 
```

Additionally the `EXTRA_ARGS` environment variable is passed with some extra arguments for the Artemis Web Console to use the certificates and enable HTTPS

```
    environment:
    - EXTRA_ARGS=--http-host 0.0.0.0 --relax-jolokia --ssl-key /etc/ssl/certs/[keystore.jks] --ssl-key-password [password] # Needed for Web Console HTTPs
```

### MQTT PG Logger 
This piece simply subscribes to topics on Artemis, and pushes whatever data it gets into a Timescale DB 

#### Configuration
All configuration is done via environment variables passed in the `docker-compose.yml` file. Development and Production configurations the same except for a single environment variable, `SSL_INSECURE` that disables SSL checks when set to `True`. For a development setup this is set to `True` by default. 

When the container is spun up, the environment variables are then used to generate a `mqtt-pg-logger.yaml` file in the `entrypoint.sh` script. Ideally down the line we should remove the configuration file genreration and update the application to just use environment variables directly. 

### Timescale DB 
This a time series database based on Postgres that stores our MQTT data. 

#### Configuration
[Official Docs for Docker](https://docs.timescale.com/self-hosted/latest/install/installation-docker/)

This is a very straightforward deployment. All configuration is done via environment variables. Development and Production configurations the same except for the fact that the data is setup to be persistent for production deployments.

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

