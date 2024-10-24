FROM python:3.12-alpine

RUN apk add --no-cache git gcc musl-dev
RUN pip install "cython<3.0.0" wheel

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
