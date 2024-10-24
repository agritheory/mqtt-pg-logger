FROM python:3.12-slim

RUN apt update && apt install -y git

RUN mkdir /mqtt-pg-logger
COPY . /mqtt-pg-logger/
COPY sql/table.sql /docker-entrypoint-initdb.d/00_table.sql
COPY sql/convert.sql /docker-entrypoint-initdb.d/01_convert.sql
COPY sql/trigger.sql /docker-entrypoint-initdb.d/02_trigger.sql

WORKDIR /mqtt-pg-logger
RUN pip install poetry
RUN poetry config virtualenvs.create false
RUN poetry install --only main --no-interaction --no-ansi

COPY entrypoint.sh /
RUN chmod +x /entrypoint.sh

EXPOSE 5432

ENTRYPOINT ["/entrypoint.sh"]
