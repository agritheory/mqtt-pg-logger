import pytest
import uvloop


@pytest.fixture(scope="session")
def event_loop_policy() -> uvloop.EventLoopPolicy:
	return uvloop.EventLoopPolicy()
