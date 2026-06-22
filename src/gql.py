import datetime
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

import httpx
import jwt  # PyJWT
import strawberry
from environs import Env
from graphql import GraphQLError
from quart import Blueprint, Response, current_app, jsonify, request
from strawberry.asgi import GraphQL
from strawberry.types import Info

from src.auth_tokens import is_token_revoked, login_rate_limiter, revoke_token
from src.passwords import hash_password, is_fernet_legacy_hash, verify_password
from src.signals import alarm_refresh_signal, topic_signal
from src.webhook_delivery import generate_signing_secret

env = Env()

graphql_bp = Blueprint("graphql", __name__)

logger = logging.getLogger(__name__)

alarm_signal = alarm_refresh_signal


@dataclass
class Context:
	user: dict | None = None


async def get_context() -> Context:
	auth_header = request.headers.get("Authorization")
	context = Context()

	if auth_header:
		try:
			scheme, token = auth_header.split()
			if scheme.lower() == "bearer":
				decoded_token = await verify_token(token)
				if decoded_token:
					context.user = decoded_token
		except ValueError:
			pass

	return context


def token_required(func: Callable) -> Callable:
	@wraps(func)
	async def wrapper(*args: Any, **kwargs: Any) -> Any:
		try:
			info: Info = kwargs["info"]
			context = info.context

			if not context.user:
				raise GraphQLError("Authorization required")
			user = await load_user_context(context.user)
			context.user = user
			return await func(*args, **kwargs)

		except GraphQLError as e:
			raise e
		except Exception as e:
			raise GraphQLError(str(e))

	return wrapper


def admin_required(func: Callable) -> Callable:
	@wraps(func)
	async def wrapper(*args: Any, **kwargs: Any) -> Any:
		info: Info = kwargs["info"]
		user = await load_user_context(info.context.user)
		if not user.is_admin:
			raise GraphQLError("Admin access required")
		info.context.user = user
		return await func(*args, **kwargs)

	return wrapper


def generate_token(username: str, expires_delta: datetime.timedelta | None = None) -> str:
	if expires_delta is None:
		expires_delta = datetime.timedelta(seconds=env.int("ACCESS_TOKEN_EXPIRES"))

	expires = datetime.datetime.now(datetime.UTC) + expires_delta
	token_data: dict = {
		"sub": username,
		"exp": expires,
		"iat": datetime.datetime.now(datetime.UTC),
		"jti": secrets.token_urlsafe(16),
	}

	token: str = jwt.encode(token_data, env.str("JWT_SECRET_KEY"), algorithm="HS256")
	return token


def decode_token(token: str) -> dict | None:
	try:
		decoded: dict = jwt.decode(
			token,
			env.str("JWT_SECRET_KEY"),
			algorithms=["HS256"],
			options={"verify_exp": True},
		)
		return decoded
	except jwt.ExpiredSignatureError:
		return None
	except jwt.InvalidTokenError:
		return None


async def verify_token(token: str) -> dict | None:
	decoded = decode_token(token)
	if not decoded:
		return None
	if await is_token_revoked(current_app.db, decoded["jti"]):
		return None
	return decoded


async def load_user_context(user_context: dict) -> "User":
	if not user_context:
		raise GraphQLError("Authorization required")

	# Convert User object to dict if needed
	if isinstance(user_context, User):
		user_context = {
			"sub": user_context.username,
			"exp": getattr(user_context, "exp", None),
			"iat": getattr(user_context, "iat", None),
			"jti": getattr(user_context, "jti", None),
		}

	username = user_context.get("sub")
	if not username:
		raise GraphQLError("Invalid token format")

	row = await current_app.db.fetchrow(
		"""
		SELECT id, username, disabled, is_admin, refresh_token, creation, modified, owner, modified_by
		FROM "user"
		WHERE username = $1
		""",
		username,
	)
	if not row:
		raise GraphQLError("User not found")

	row_dict = dict(row)
	if row_dict.get("refresh_token") is not None:
		row_dict["refresh_token"] = bytes(row_dict["refresh_token"])
	user_data = {**row_dict, **user_context}
	user = User(**user_data)

	if user.disabled:
		raise GraphQLError("User is disabled")

	return user


@dataclass
@strawberry.type
class User:
	id: int
	username: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner: str
	modified_by: str
	is_admin: bool = False
	refresh_token: strawberry.Private[str | None] = None
	sub: strawberry.Private[str | None] = None
	exp: strawberry.Private[int | None] = None
	iat: strawberry.Private[int | None] = None
	jti: strawberry.Private[str | None] = None


@dataclass
@strawberry.type
class AuthResponse:
	message: str
	access_token: str
	refresh_token: str
	token_type: str
	expires_in: int


@strawberry.input
class LoginInput:
	username: str
	password: str


@strawberry.input
class RefreshTokenInput:
	refresh_token: str


@dataclass
@strawberry.type
class Topic:
	id: int
	topic: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner: str
	modified_by: str


@strawberry.input
class TopicInput:
	topic: str
	disabled: bool = False


@strawberry.input
class UserInput:
	username: str
	password: str
	disabled: bool = False


@dataclass
@strawberry.type
class Health:
	status: str
	timestamp: datetime.datetime
	timescaledb_status: str
	artemis_status: str
	mqtt_connection: str


@dataclass
@strawberry.type
class JournalEntry:
	id: int
	topic: str
	text: str
	data: strawberry.scalars.JSON | None
	creation: datetime.datetime


@dataclass
@strawberry.type
class Webhook:
	id: int
	name: str
	url: str
	signing_secret: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner_id: int
	modified_by_id: int


@strawberry.input
class WebhookInput:
	name: str
	url: str
	disabled: bool = False


@dataclass
@strawberry.type
class Alarm:
	id: int
	condition: str
	owner: str
	creation: datetime.datetime
	modified: datetime.datetime
	modified_by: str
	disabled: bool
	topic: str
	alarm_name: str
	delivery_method: str
	webhook_id: int | None
	forward_topic: str | None = None


@strawberry.input
class AlarmInput:
	condition: str
	owner: str
	modified_by: str
	topic: str
	alarm_name: str
	delivery_method: str
	disabled: bool = False
	id: int | None = None
	webhook_id: int | None = None
	forward_topic: str | None = None


@strawberry.type
class Query:
	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_topics(self, info: Info[Context, Any]) -> list[Topic]:
		rows = await current_app.db.fetch(
			"""
			SELECT id, topic, disabled, creation, modified, owner, modified_by
			FROM topic
			WHERE disabled = false
			ORDER BY topic
			"""
		)
		return [Topic(**dict(row)) for row in rows]

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_topic(self, info: Info, topic_id: int) -> Topic | None:
		row = await current_app.db.fetchrow(
			"""
			SELECT id, topic, disabled, creation, modified, owner, modified_by
			FROM topic
			WHERE id = $1
			""",
			topic_id,
		)
		return Topic(**dict(row)) if row else None

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_users(self, info: Info) -> list[User]:
		rows = await current_app.db.fetch(
			"""
			SELECT id, username, disabled, is_admin, creation, modified, owner, modified_by
			FROM "user"
			WHERE disabled = false
			ORDER BY username
			"""
		)
		return [User(**dict(row)) for row in rows]

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_user(self, info: Info, user_id: int) -> User | None:
		row = await current_app.db.fetchrow(
			"""
			SELECT id, username, disabled, is_admin, creation, modified, owner, modified_by
			FROM "user"
			WHERE id = $1
			""",
			user_id,
		)
		return User(**dict(row)) if row else None

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def health(self, info: Info[Context, Any]) -> Health:
		env = Env()
		health_status = Health(
			status="ok",
			timestamp=datetime.datetime.now(datetime.UTC),
			timescaledb_status="ok",
			artemis_status="ok",
			mqtt_connection="ok",
		)

		if hasattr(current_app, "db"):
			try:
				await current_app.db.fetchval("SELECT 1")
			except Exception as e:
				health_status.status = "error"
				health_status.timescaledb_status = str(e)

		if hasattr(current_app, "mqtt_logger"):
			if not current_app.mqtt_logger.is_connected():
				health_status.status = "error"
				health_status.mqtt_connection = "disconnected"

		mqtt_broker_url = env.str("MQTT_BROKER_HOST", "artemis")
		mqtt_broker_web_console_port = env.int("MQTT_BROKER_WEB_CONSOLE_PORT", 8161)
		try:
			async with httpx.AsyncClient() as client:
				response = await client.get(
					f"http://{mqtt_broker_url}:{mqtt_broker_web_console_port}/",
					follow_redirects=True,
				)
				if response.status_code != 200:
					health_status.status = "error"
					health_status.artemis_status = f"Artemis UI responded with status code {response.status_code}"
		except Exception as e:
			health_status.status = "error"
			health_status.artemis_status = str(e)

		return health_status

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_journal_entries(
		self,
		info: Info,
		topic: str | None = None,
		start_time: datetime.datetime | None = None,
		end_time: datetime.datetime | None = None,
		limit: int = 100,
	) -> list[JournalEntry]:
		args: list[Any] = []
		conditions: list[str] = []

		if topic is not None:
			args.append(topic)
			conditions.append(f"topic = ${len(args)}")

		if start_time is not None:
			args.append(start_time)
			conditions.append(f"creation >= ${len(args)}")

		if end_time is not None:
			args.append(end_time)
			conditions.append(f"creation <= ${len(args)}")

		args.append(min(limit, 1000))
		where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

		query = f"""
			SELECT id, topic, text, data, creation
			FROM journal
			{where_clause}
			ORDER BY creation DESC
			LIMIT ${len(args)}
		"""

		rows = await current_app.db.fetch(query, *args)
		return [JournalEntry(**dict(row)) for row in rows]

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def webhook(self, info: Info, id: int) -> Webhook | None:
		"""Get a single webhook by ID"""
		row = await current_app.db.fetchrow(
			"""
			SELECT id, name, url, signing_secret, disabled, creation, modified, owner_id, modified_by_id
			FROM webhook
			WHERE id = $1
			""",
			id,
		)
		return Webhook(**dict(row)) if row else None

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_webhooks(
		self,
		info: Info,
		disabled: bool | None = None,
	) -> list[Webhook]:
		args: list[Any] = []
		conditions: list[str] = []
		if disabled is not None:
			args.append(disabled)
			conditions.append(f"disabled = ${len(args)}")
		where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
		query = f"""
			SELECT id, name, url, signing_secret, disabled, creation, modified, owner_id, modified_by_id
			FROM webhook
			{where_clause}
			ORDER BY modified DESC
		"""
		rows = await current_app.db.fetch(query, *args)
		return [Webhook(**dict(row)) for row in rows]

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def alarm(self, info: Info, id: int) -> Alarm | None:
		"""Get a single alarm by ID"""
		row = await current_app.db.fetchrow(
			"""
			SELECT id, condition, owner, creation, modified, modified_by,
				disabled, topic, alarm_name, delivery_method, webhook_id, forward_topic
			FROM alarm
			WHERE id = $1
			""",
			id,
		)
		return Alarm(**dict(row)) if row else None

	@strawberry.field  # type: ignore[misc]
	@token_required
	async def get_alarms(
		self,
		info: Info,
		owner: str | None = None,
		topic: str | None = None,
		disabled: bool | None = None,
	) -> list[Alarm]:
		args: list[Any] = []
		conditions: list[str] = []

		if owner is not None:
			args.append(owner)
			conditions.append(f"owner = ${len(args)}")

		if topic is not None:
			args.append(topic)
			conditions.append(f"topic = ${len(args)}")

		if disabled is not None:
			args.append(disabled)
			conditions.append(f"disabled = ${len(args)}")

		where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
		query = f"""
			SELECT id, condition, owner, creation, modified, modified_by,
				disabled, topic, alarm_name, delivery_method, webhook_id, forward_topic
			FROM alarm
			{where_clause}
			ORDER BY modified DESC
		"""

		rows = await current_app.db.fetch(query, *args)
		return [Alarm(**dict(row)) for row in rows]


@strawberry.type
class Mutation:
	@strawberry.mutation  # type: ignore[misc]
	async def login(self, info: Info[Context, Any], input: LoginInput) -> AuthResponse:
		client_ip = request.remote_addr or "unknown"
		rate_key = f"{input.username}:{client_ip}"
		if login_rate_limiter.is_blocked(rate_key):
			raise GraphQLError("Invalid credentials")

		user = await current_app.db.fetchrow(
			"""
			SELECT id, username, password_hash, disabled
			FROM "user"
			WHERE username = $1
			AND disabled = FALSE
			""",
			input.username,
		)

		if not user:
			login_rate_limiter.record_failure(rate_key)
			raise GraphQLError("Invalid credentials")

		if user["disabled"]:
			login_rate_limiter.record_failure(rate_key)
			raise GraphQLError("Account is disabled")

		env = Env()
		stored_hash = user["password_hash"]
		fernet_key = env.str("FERNET_KEY", None)
		if not verify_password(stored_hash, input.password, fernet_key=fernet_key):
			login_rate_limiter.record_failure(rate_key)
			raise GraphQLError("Invalid credentials")

		login_rate_limiter.clear(rate_key)

		if stored_hash and is_fernet_legacy_hash(stored_hash):
			await current_app.db.execute(
				'UPDATE "user" SET password_hash = $1 WHERE id = $2',
				hash_password(input.password),
				user["id"],
			)

		access_token = generate_token(user["username"])
		refresh_token = generate_token(
			user["username"],
			expires_delta=datetime.timedelta(seconds=env.int("REFRESH_TOKEN_EXPIRES")),
		)

		await current_app.db.execute(
			'UPDATE "user" SET refresh_token = $1 WHERE id = $2',
			bytes(refresh_token.encode()),
			user["id"],
		)

		return AuthResponse(
			message="Login successful",
			access_token=access_token,
			refresh_token=refresh_token,
			token_type="bearer",
			expires_in=env.int("ACCESS_TOKEN_EXPIRES"),
		)

	@strawberry.mutation  # type: ignore[misc]
	async def refresh_token(self, info: Info[Context, Any], input: RefreshTokenInput) -> AuthResponse:
		env = Env()
		decoded = decode_token(input.refresh_token)
		if not decoded:
			raise GraphQLError("Invalid refresh token")
		if await is_token_revoked(current_app.db, decoded["jti"]):
			raise GraphQLError("Invalid refresh token")

		username = decoded.get("sub")
		if not username:
			raise GraphQLError("Invalid refresh token")

		user_row = await current_app.db.fetchrow(
			"""
			SELECT id, username, refresh_token, disabled
			FROM "user"
			WHERE username = $1
			""",
			username,
		)
		if not user_row or user_row["disabled"]:
			raise GraphQLError("Invalid refresh token")

		stored_refresh_token = user_row["refresh_token"].decode() if user_row["refresh_token"] else None
		if not stored_refresh_token or stored_refresh_token != input.refresh_token:
			raise GraphQLError("Invalid refresh token")

		new_access_token = generate_token(user_row["username"])
		new_refresh_token = generate_token(
			user_row["username"],
			expires_delta=datetime.timedelta(seconds=env.int("REFRESH_TOKEN_EXPIRES")),
		)

		await current_app.db.execute(
			'UPDATE "user" SET refresh_token = $1 WHERE id = $2',
			bytes(new_refresh_token.encode()),
			user_row["id"],
		)

		return AuthResponse(
			message="Token refresh successful",
			access_token=new_access_token,
			refresh_token=new_refresh_token,
			token_type="bearer",
			expires_in=env.int("ACCESS_TOKEN_EXPIRES"),
		)

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	async def logout(self, info: Info[Context, Any]) -> bool:
		user = await load_user_context(info.context.user)
		exp = user.exp
		if exp is None:
			raise GraphQLError("Invalid token")
		expires_at = datetime.datetime.fromtimestamp(int(exp), tz=datetime.UTC)
		if user.jti is None:
			raise GraphQLError("Invalid token")
		await revoke_token(current_app.db, user.jti, expires_at)
		return True

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	async def create_topic(self, info: Info, input: TopicInput) -> Topic:
		user = await load_user_context(info.context.user)
		row = await current_app.db.fetchrow(
			"""
			INSERT INTO topic (topic, disabled, owner, modified_by)
			VALUES ($1, $2, $3, $4)
			ON CONFLICT (topic) DO UPDATE
			SET disabled    = EXCLUDED.disabled,
				owner       = EXCLUDED.owner,
				modified_by = EXCLUDED.modified_by,
				modified    = NOW()
			RETURNING id, topic, disabled, creation, modified, owner, modified_by
			""",
			str(input.topic),
			bool(input.disabled),
			user.username,
			user.username,
		)
		await topic_signal.send_async("add_topic", topic=str(input.topic))
		return Topic(**dict(row))

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	async def update_topic(self, info: Info, id: int, input: TopicInput) -> Topic:
		user = await load_user_context(info.context.user)
		row = await current_app.db.fetchrow(
			"""
			UPDATE topic
			SET topic = $1,
				disabled = $2,
				modified = CURRENT_TIMESTAMP,
				modified_by = $3
			WHERE id = $4
			RETURNING id, topic, disabled, creation, modified, owner, modified_by
			""",
			input.topic,
			input.disabled,
			user.username,
			id,
		)
		await topic_signal.send_async("add_topic", topic=str(input.topic))
		return Topic(**dict(row))

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	@admin_required
	async def create_user(self, info: Info, input: UserInput) -> User:
		user = await load_user_context(info.context.user)

		password_hash = hash_password(input.password) if input.password else None
		row = await current_app.db.fetchrow(
			"""
			INSERT INTO "user" (username, password_hash, disabled, is_admin, owner, modified_by)
			VALUES ($1, $2, $3, false, $4, $5)
			RETURNING id, username, disabled, is_admin, creation, modified, owner, modified_by
			""",
			input.username,
			password_hash,
			input.disabled,
			user.username,
			user.username,
		)
		return User(**dict(row))

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	@admin_required
	async def update_user(self, info: Info, id: int, input: UserInput) -> User:
		user = await load_user_context(info.context.user)
		row = await current_app.db.fetchrow(
			"""
			UPDATE "user"
			SET username = $1,
				password_hash = $2,
				disabled = $3,
				modified = CURRENT_TIMESTAMP,
				modified_by = $4
			WHERE id = $5
			RETURNING id, username, disabled, is_admin, creation, modified, owner, modified_by
			""",
			input.username,
			hash_password(input.password),
			input.disabled,
			user.username,
			id,
		)
		return User(**dict(row))

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	async def create_webhook(self, info: Info, input: WebhookInput) -> Webhook:
		user = await load_user_context(info.context.user)
		if not user.is_admin:
			raise GraphQLError("Admin access required")
		signing_secret = generate_signing_secret()
		row = await current_app.db.fetchrow(
			"""
			INSERT INTO webhook (name, url, signing_secret, disabled, owner_id, modified_by_id)
			VALUES ($1, $2, $3, $4, $5, $6)
			RETURNING id, name, url, signing_secret, disabled, creation, modified, owner_id, modified_by_id
			""",
			input.name,
			input.url,
			signing_secret,
			input.disabled,
			user.id,
			user.id,
		)
		return Webhook(**dict(row))

	@strawberry.mutation  # type: ignore[misc]
	@token_required
	async def alarm(self, info: Info, input: AlarmInput) -> Alarm:
		user = await load_user_context(info.context.user)

		if input.id is not None:
			existing = await current_app.db.fetchrow(
				"SELECT owner FROM alarm WHERE id = $1",
				input.id,
			)
			if not existing:
				raise GraphQLError("Alarm not found")
			if not user.is_admin and existing["owner"] != user.username:
				raise GraphQLError("Not authorized to modify this alarm")
		elif not user.is_admin and input.owner != user.username:
			raise GraphQLError("Not authorized to create alarms for another owner")

		if input.delivery_method == "mqtt":
			if not input.forward_topic:
				raise GraphQLError("forward_topic is required when delivery_method is 'mqtt'")
			if input.forward_topic == input.topic:
				raise GraphQLError(
					"forward_topic cannot equal topic: forwarding a message back to its own "
					"source topic would create an infinite loop"
				)

		if input.id is None:
			row = await current_app.db.fetchrow(
				"""
				INSERT INTO alarm (
					condition, owner, modified_by, topic,
					alarm_name, delivery_method, disabled, webhook_id, forward_topic
				)
				VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
				RETURNING id, condition, owner, creation, modified, modified_by,
					disabled, topic, alarm_name, delivery_method, webhook_id, forward_topic
				""",
				input.condition,
				input.owner,
				user.username,
				input.topic,
				input.alarm_name,
				input.delivery_method,
				input.disabled,
				input.webhook_id,
				input.forward_topic,
			)
		else:
			row = await current_app.db.fetchrow(
				"""
				UPDATE alarm
				SET condition = $1,
					owner = $2,
					modified_by = $3,
					topic = $4,
					alarm_name = $5,
					delivery_method = $6,
					disabled = $7,
					webhook_id = $8,
					forward_topic = $9,
					modified = CURRENT_TIMESTAMP
				WHERE id = $10
				RETURNING id, condition, owner, creation, modified, modified_by,
					disabled, topic, alarm_name, delivery_method, webhook_id, forward_topic
				""",
				input.condition,
				input.owner,
				user.username,
				input.topic,
				input.alarm_name,
				input.delivery_method,
				input.disabled,
				input.webhook_id,
				input.forward_topic,
				input.id,
			)

		await alarm_signal.send_async("refresh_alarms")
		return Alarm(**dict(row))


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_app = GraphQL(schema)


@graphql_bp.route("/", methods=["GET", "POST"])  # type: ignore[misc]
async def graphql_handler() -> Response:
	if request.method == "GET":
		return Response(
			"""
<!DOCTYPE html>
<html>
	<head>
		<title>GraphiQL</title>
		<style>
			body { margin: 0; padding: 0; min-height: 100vh; }
			#graphiql { height: 100vh; }
		</style>
		<script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
		<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
		<link rel="stylesheet" href="https://unpkg.com/graphiql@3/graphiql.min.css" />
		<script src="https://unpkg.com/graphiql@3/graphiql.min.js"></script>
	</head>
	<body>
		<div id="graphiql"></div>
		<script>
			const root = ReactDOM.createRoot(document.getElementById('graphiql'));
			root.render(
				React.createElement(GraphiQL, {
					fetcher: GraphiQL.createFetcher({
						url: window.location.href,
					}),
				})
			);
		</script>
	</body>
</html>
			""",
			status=200,
			headers={"Content-Type": "text/html"},
		)

	if request.headers.get("Content-Type", "").startswith("application/json"):
		data = await request.get_json()
		context = await get_context()

		result = await schema.execute(
			query=data.get("query"),
			variable_values=data.get("variables"),
			context_value=context,
			operation_name=data.get("operationName"),
		)

		response: dict = {}
		if result.data is not None:
			response["data"] = result.data
		if result.errors:
			response["errors"] = [{"message": str(err)} for err in result.errors]
		return jsonify(response)

	return jsonify({"errors": [{"message": "Invalid Content-Type"}]}), 400
