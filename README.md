# mqtt-pg-logger

Collects [MQTT](https://en.wikipedia.org/wiki/MQTT) messages and stores them in a Postgres database.

- Runs as Linux service.
- Provides the message payload as standard VARCHAR text and additionally converts the payload into a JSONB column if compatible. (See: [trigger.sql](./sql/trigger.sql) and [convert.sql](./sql/convert.sql))
- Clean up old messages (after x days).

## Docker

### Quickstart 

```docker-compose up```

### Configuration 
The docker-compose file is setup to run 
- mqtt-pg-logger
- ActiveMQ Artemis
- TimescaleDB 

All configuration is done via env vars in the docker-compose file, and default configuration is setup to just work out of the box. 

**Ports** 
- `8161` - Artemis Web UI 
- `616161` - Artemis main messaging port, accepts all protocols 
- `1883` - Artemis MQTT port

### Development 
When developing mqtt-pg-logger code make sure to rebuild the docker image after making code changes if everything is running in docker. 

```
docker-compose build mqtt-pg-logger
docker-compose down 
docker-compose up
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

# check python version >= 3.7
python --version

# install required packages
pip install --upgrade -r requirements.txt
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
# alternativ: sudo cp ./mqtt-pg-logger.service.sample /etc/systemd/system//mqtt-pg-logger.service
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
