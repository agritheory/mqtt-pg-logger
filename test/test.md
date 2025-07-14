# Testing
## test_db
The `test_db` uses `testcontainers` to spin up a PostgreSQL container for testing purposes. This is a clean separate from the main database that is spun up when testing starts in the `conftest.py` file.
## Usage
run `pytest` to run the tests.

## publishing messages
Messages are published via the `load_cell_example_data.py` file. This file contains a `LoadCellPublisher` class that publishes messages to the MQTT broker.

## test_throughput
Tests in `test_throughput.py` and `test_alarm_latency` contain tests to characterize the speed of the system. The parameters chosen to characterize the speed are:
- RAM usage
- Time to publish n messages on the mqtt broker
- Latency of the alarm system

These types of tests are useful for understanding the performance of the system, however interpretations in: the speed of the handshakes, how sensors are expected to connect, and required performance of the system should be considered in future baselines.