import os
from typing import Any

import pytest
from cryptography.fernet import Fernet
from quart.testing import QuartClient

from src.passwords import hash_password, verify_password


def test_argon2_hash_and_verify() -> None:
	hashed = hash_password("secret-password")
	assert hashed.startswith("$argon2")
	assert verify_password(hashed, "secret-password")
	assert not verify_password(hashed, "wrong-password")


@pytest.mark.asyncio  # type: ignore[misc]
async def test_fernet_password_upgrades_on_login(
	app: Any, execute_graphql: Any, login_mutation: str
) -> None:
	pool = app.app.db
	fernet = Fernet(os.environ["FERNET_KEY"])
	legacy_hash = fernet.encrypt(b"ohch4GeiSie").decode("utf-8")
	await pool.execute(
		'UPDATE "user" SET password_hash = $1 WHERE username = $2',
		legacy_hash,
		os.environ["ADMIN_EMAIL"],
	)

	response = await execute_graphql(login_mutation)
	assert "data" in response

	row = await pool.fetchrow(
		'SELECT password_hash FROM "user" WHERE username = $1',
		os.environ["ADMIN_EMAIL"],
	)
	assert row is not None
	assert str(row["password_hash"]).startswith("$argon2")


@pytest.mark.asyncio  # type: ignore[misc]
async def test_health_query(
	test_client: QuartClient, login_mutation: str, execute_graphql: Any
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	response = await execute_graphql(
		"""
		query {
			health {
				status
				timestamp
				mqttConnection
				timescaledbStatus
				artemisStatus
			}
		}
		""",
		token=token,
	)
	assert "data" in response
	assert "health" in response["data"]
	assert response["data"]["health"]["status"] in ("ok", "error")
	assert response["data"]["health"]["timescaledbStatus"] == "ok"


@pytest.mark.asyncio  # type: ignore[misc]
async def test_non_admin_cannot_create_user(
	app: Any, execute_graphql: Any, login_mutation: str
) -> None:
	pool = app.app.db
	await pool.execute(
		"""
		INSERT INTO "user" (username, password_hash, disabled, is_admin, owner, modified_by)
		VALUES ($1, $2, false, false, $1, $1)
		""",
		"regular@example.com",
		hash_password("regular-pass"),
	)

	login_response = await execute_graphql(
		"""
		mutation {
			login(input: {username: "regular@example.com", password: "regular-pass"}) {
				accessToken
			}
		}
		"""
	)
	token = login_response["data"]["login"]["accessToken"]

	response = await execute_graphql(
		"""
		mutation {
			createUser(input: {username: "new@example.com", password: "pass1234"}) {
				id
			}
		}
		""",
		token=token,
	)
	assert "errors" in response
	assert "Admin access required" in response["errors"][0]["message"]
