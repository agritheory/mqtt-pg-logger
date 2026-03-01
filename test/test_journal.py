import datetime
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from quart.testing import QuartClient, TestApp

GET_JOURNAL_ENTRIES = """
query GetJournalEntries(
    $topic: String,
    $startTime: DateTime,
    $endTime: DateTime,
    $limit: Int
) {
  getJournalEntries(
      topic: $topic,
      startTime: $startTime,
      endTime: $endTime,
      limit: $limit
  ) {
    id
    topic
    text
    data
    creation
  }
}
"""

_INSERT_JOURNAL = """
    INSERT INTO journal (topic, text, entrypoint, priority)
    VALUES (:topic, :text, :entrypoint, :priority)
    RETURNING id, creation
"""

_INSERT_JOURNAL_WITH_TIME = """
    INSERT INTO journal (topic, text, entrypoint, priority, creation)
    VALUES (:topic, :text, :entrypoint, :priority, :creation)
    RETURNING id, creation
"""


async def _insert(db: Any, topic: str, text: str) -> dict[str, Any]:
	row = await db.fetch_one(
		query=_INSERT_JOURNAL,
		values={"topic": topic, "text": text, "entrypoint": "test", "priority": 0},
	)
	return dict(row)


async def _insert_at(
	db: Any, topic: str, text: str, creation: datetime.datetime
) -> dict[str, Any]:
	row = await db.fetch_one(
		query=_INSERT_JOURNAL_WITH_TIME,
		values={"topic": topic, "text": text, "entrypoint": "test", "priority": 0, "creation": creation},
	)
	return dict(row)


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_empty(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""Returns an empty list when the journal has no entries."""
	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(GET_JOURNAL_ENTRIES, token=token)
	assert "data" in resp
	assert resp["data"]["getJournalEntries"] == []


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_returns_entries(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""Entries inserted into the journal table are returned by getJournalEntries."""
	db = app.app.db
	await _insert(db, "sensors/temperature", '{"temp": 22.5}')
	await _insert(db, "sensors/temperature", '{"temp": 23.0}')

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(GET_JOURNAL_ENTRIES, token=token)
	entries = resp["data"]["getJournalEntries"]
	assert len(entries) == 2
	assert all(e["topic"] == "sensors/temperature" for e in entries)
	texts = {e["text"] for e in entries}
	assert texts == {'{"temp": 22.5}', '{"temp": 23.0}'}


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_filter_by_topic(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""topic filter returns only entries whose topic matches exactly."""
	db = app.app.db
	await _insert(db, "sensors/temperature", '{"temp": 22.5}')
	await _insert(db, "sensors/humidity", '{"humidity": 60}')
	await _insert(db, "sensors/temperature", '{"temp": 23.0}')

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		GET_JOURNAL_ENTRIES, token=token, variables={"topic": "sensors/temperature"}
	)
	entries = resp["data"]["getJournalEntries"]
	assert len(entries) == 2
	assert all(e["topic"] == "sensors/temperature" for e in entries)


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_filter_by_topic_no_match(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""topic filter returns empty list when no entries match."""
	db = app.app.db
	await _insert(db, "sensors/temperature", '{"temp": 22.5}')

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		GET_JOURNAL_ENTRIES, token=token, variables={"topic": "sensors/pressure"}
	)
	assert resp["data"]["getJournalEntries"] == []


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_limit(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""limit parameter caps the number of entries returned."""
	db = app.app.db
	for i in range(5):
		await _insert(db, "sensors/pressure", f'{{"pressure": {100 + i}}}')

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(GET_JOURNAL_ENTRIES, token=token, variables={"limit": 3})
	assert len(resp["data"]["getJournalEntries"]) == 3


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_filter_start_time(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""startTime filter excludes entries created before the cutoff."""
	db = app.app.db
	now = datetime.datetime.now(datetime.UTC)
	two_hours_ago = now - datetime.timedelta(hours=2)
	cutoff = now - datetime.timedelta(hours=1)

	await _insert_at(db, "sensors/flow", '{"flow": 1.0}', two_hours_ago)
	await _insert_at(db, "sensors/flow", '{"flow": 2.0}', now)

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		GET_JOURNAL_ENTRIES,
		token=token,
		variables={"topic": "sensors/flow", "startTime": cutoff.isoformat()},
	)
	entries = resp["data"]["getJournalEntries"]
	assert len(entries) == 1
	assert entries[0]["text"] == '{"flow": 2.0}'


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_filter_end_time(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""endTime filter excludes entries created after the cutoff."""
	db = app.app.db
	now = datetime.datetime.now(datetime.UTC)
	two_hours_ago = now - datetime.timedelta(hours=2)
	cutoff = now - datetime.timedelta(hours=1)

	await _insert_at(db, "sensors/flow", '{"flow": 1.0}', two_hours_ago)
	await _insert_at(db, "sensors/flow", '{"flow": 2.0}', now)

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	resp = await execute_graphql(
		GET_JOURNAL_ENTRIES,
		token=token,
		variables={"topic": "sensors/flow", "endTime": cutoff.isoformat()},
	)
	entries = resp["data"]["getJournalEntries"]
	assert len(entries) == 1
	assert entries[0]["text"] == '{"flow": 1.0}'


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_filter_time_range(
	app: TestApp,
	test_client: QuartClient,
	login_mutation: str,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""Combining startTime and endTime returns only entries within the window."""
	db = app.app.db
	now = datetime.datetime.now(datetime.UTC)

	await _insert_at(db, "sensors/flow", '{"flow": 1.0}', now - datetime.timedelta(hours=3))
	await _insert_at(db, "sensors/flow", '{"flow": 2.0}', now - datetime.timedelta(hours=2))
	await _insert_at(db, "sensors/flow", '{"flow": 3.0}', now - datetime.timedelta(hours=1))
	await _insert_at(db, "sensors/flow", '{"flow": 4.0}', now)

	login_resp = await execute_graphql(login_mutation)
	token = login_resp["data"]["login"]["accessToken"]

	start = (now - datetime.timedelta(hours=2, minutes=30)).isoformat()
	end = (now - datetime.timedelta(minutes=30)).isoformat()

	resp = await execute_graphql(
		GET_JOURNAL_ENTRIES,
		token=token,
		variables={"topic": "sensors/flow", "startTime": start, "endTime": end},
	)
	entries = resp["data"]["getJournalEntries"]
	assert len(entries) == 2
	import json as _json

	flows = {_json.loads(e["text"])["flow"] for e in entries}
	assert flows == {2.0, 3.0}


@pytest.mark.asyncio  # type: ignore[misc]
async def test_journal_requires_auth(
	test_client: QuartClient,
	execute_graphql: Callable[..., Awaitable[dict[str, Any]]],
) -> None:
	"""getJournalEntries requires a valid token."""
	resp = await execute_graphql(GET_JOURNAL_ENTRIES)
	assert "errors" in resp
	assert "Authorization required" in resp["errors"][0]
