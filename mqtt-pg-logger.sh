#!/usr/bin/env bash

export PYTHONUNBUFFERED=1

# change into script dir to use relative pathes
SCRIPT_PATH=$(readlink -f $0)
SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
SCRIPT_NAME=$(basename $0)
cd "$SCRIPT_DIR"

export PYTHONPATH="$SCRIPT_DIR"

python ./src/mqtt_pg_logger.py $@
exit $?
