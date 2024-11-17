import datetime
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Literal

import httpx
import jwt  # PyJWT
import strawberry
from blinker import signal
from cryptography.fernet import Fernet
from environs import Env
from graphql import GraphQLError
from quart import Blueprint, Response, current_app, jsonify, request
from strawberry.asgi import GraphQL
from strawberry.types import Info

env = Env()

graphql_bp = Blueprint("graphql", __name__)

token_blacklist = set()

_logger = logging.getLogger(__name__)

topic_signal = signal("topic")
alarm_signal = signal("refresh_alarms")


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
				decoded_token = verify_token(token)
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


def generate_token(username, users, expires_delta=None):
	if expires_delta is None:
		expires_delta = datetime.timedelta(seconds=env.int("ACCESS_TOKEN_EXPIRES"))

	expires = datetime.datetime.now(datetime.UTC) + expires_delta
	token_data = {
		"sub": username,
		"exp": expires,
		"iat": datetime.datetime.now(datetime.UTC),
		"jti": secrets.token_urlsafe(16),
	}

	return jwt.encode(token_data, env.str("JWT_SECRET_KEY"), algorithm="HS256")


def verify_token(token):
	try:
		decoded = jwt.decode(
			token,
			env.str("JWT_SECRET_KEY"),
			algorithms=["HS256"],
			options={"verify_exp": True},
		)
		if decoded["jti"] in token_blacklist:
			return None
		return decoded
	except jwt.ExpiredSignatureError:
		return None
	except jwt.InvalidTokenError:
		return None


async def load_user_context(user_context):
	query = """
	SELECT id, username, disabled, refresh_token, creation, modified, owner, modified_by
	FROM "user"
	WHERE username = :username
	"""
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

	# Handle dictionary case
	username = user_context.get("sub")
	if not username:
		raise GraphQLError("Invalid token format")

	row = await current_app.db.fetch_one(query=query, values={"username": username})
	if not row:
		raise GraphQLError("User not found")

	# Convert row to dict and merge with user_context
	row_dict = dict(row)
	user_data = {**row_dict, **user_context}
	user = User(**user_data)

	if user.disabled:
		raise GraphQLError("User is disabled")

	return user


@strawberry.type
class User:
	id: int
	username: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner: str
	modified_by: str
	refresh_token: strawberry.Private[object]
	sub: strawberry.Private[object]
	exp: strawberry.Private[object]
	iat: strawberry.Private[object]
	jti: strawberry.Private[object]


@strawberry.type(init=True)
@dataclass  # typing helper
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


@strawberry.type(init=True)
@dataclass  # typing helper
class Health:
	status: Literal["ok", "error"]
	timestamp: datetime.datetime
	timescaledb_status: str
	artemis_status: str
	mqtt_connection: str


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


@strawberry.type
class Query:
	@strawberry.field
	@token_required
	async def get_topics(self, info: Info[Context, Any]) -> list[Topic]:
		query = """
			SELECT id, topic, disabled, creation, modified, owner, modified_by
			FROM topic
			WHERE disabled = false
			ORDER BY topic
		"""
		rows = await current_app.db.fetch_all(query=query)
		return [Topic(**row) for row in rows]

	@strawberry.field
	@token_required
	async def get_topic(self, info: Info, topic_id: int) -> Topic | None:
		query = """
		SELECT id, topic, disabled, creation, modified, owner, modified_by
		FROM topic
		WHERE id = :topic_id
		"""
		row = await current_app.db.fetch_one(query=query, values={"topic_id": topic_id})
		return Topic(**row) if row else None

	@strawberry.field
	@token_required
	async def get_users(self, info: Info) -> list[User]:
		query = """
		SELECT id, username, disabled, creation, modified, owner, modified_by
		FROM "user"
		WHERE disabled = false
		ORDER BY username
		"""
		rows = await current_app.db.fetch_all(query=query)
		return [User(**row) for row in rows]

	@strawberry.field
	@token_required
	async def get_user(self, info: Info, user_id: int) -> User | None:
		query = """
		SELECT id, username, disabled, creation, modified, owner, modified_by
		FROM "user"
		WHERE id = :user_id
		"""
		row = await current_app.db.fetch_one(query=query, values={"user_id": user_id})
		return User(**row) if row else None

	@strawberry.field
	@token_required
	async def health(self, info: Info[Context, Any]) -> Health:
		env = Env()
		health_status = Health(
			status="ok",
			timestamp=datetime.datetime.utcnow(),
			timescaledb_status="ok",
			artemis_status="ok",
			mqtt_connection="ok",
		)

		if hasattr(current_app, "db"):
			try:
				await current_app.db.execute("SELECT 1")
			except Exception as e:
				health_status.status = "error"
				health_status.timescaledb_status = str(e)

		if hasattr(current_app, "mqtt_logger"):
			if not current_app.mqtt_logger.client.is_connected():
				health_status.status = "error"
				health_status.mqtt_connection = "disconnected"

		mqtt_broker_url = env.str("MQTT_BROKER_HOST ", "artemis")
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

	@strawberry.field
	@token_required
	async def alarm(self, info: Info, id: int) -> Alarm | None:
		"""Get a single alarm by ID"""
		query = """
			SELECT id, condition, owner, creation, modified, modified_by,
				disabled, topic, alarm_name, delivery_method
			FROM alarm
			WHERE id = :alarm_id
		"""
		row = await current_app.db.fetch_one(query=query, values={"alarm_id": id})
		return Alarm(**dict(row)) if row else None

	@strawberry.field
	@token_required
	async def get_alarms(
		self,
		info: Info,
		owner: str | None = None,
		topic: str | None = None,
		disabled: bool | None = None,
	) -> list[Alarm]:

		conditions = []
		values = {}
		if owner is not None:
			conditions.append("owner = :owner")
			values["owner"] = owner

		if topic is not None:
			conditions.append("topic = :topic")
			values["topic"] = topic

		if disabled is not None:
			conditions.append("disabled = :disabled")
			values["disabled"] = str(disabled)

		where_clause = " AND ".join(conditions)
		where_clause = f"WHERE {where_clause}" if where_clause else ""

		query = f"""
			SELECT id, condition, owner, creation, modified, modified_by,
				disabled, topic, alarm_name, delivery_method
			FROM alarm
			{where_clause}
			ORDER BY modified DESC
		"""

		rows = await current_app.db.fetch_all(query=query, values=values)
		return [Alarm(**dict(row)) for row in rows]


@strawberry.type
class Mutation:
	@strawberry.mutation
	async def login(self, info: Info[Context, Any], input: LoginInput) -> AuthResponse:
		query = """
			SELECT id, username, password_hash, disabled
			FROM "user"
			WHERE username = :username
			AND disabled = FALSE
		"""
		user = await current_app.db.fetch_one(query=query, values={"username": input.username})

		if not user:
			raise GraphQLError("Invalid credentials")

		if user["disabled"]:
			raise GraphQLError("Account is disabled")

		env = Env()
		f = Fernet(env.str("FERNET_KEY").encode())

		stored_hash = bytes(user["password_hash"])
		try:
			decrypted_password = f.decrypt(stored_hash).decode()
			if decrypted_password != input.password:
				raise GraphQLError("Invalid credentials")
		except Exception:
			raise GraphQLError("Invalid credentials")

		access_token = generate_token(user["username"], {})
		refresh_token = generate_token(
			user["username"], {}, expires_delta=datetime.timedelta(seconds=env.int("REFRESH_TOKEN_EXPIRES"))
		)

		await current_app.db.execute(
			'UPDATE "user" SET refresh_token = :refresh_token WHERE id = :user_id',
			values={"refresh_token": bytes(refresh_token.encode()), "user_id": user["id"]},
		)

		return AuthResponse(
			message="Login successful",
			access_token=access_token,
			refresh_token=refresh_token,
			token_type="bearer",
			expires_in=env.int("ACCESS_TOKEN_EXPIRES"),
		)

	@strawberry.mutation
	async def refresh_token(self, info: Info[Context, Any], input: RefreshTokenInput) -> AuthResponse:
		env = Env()
		try:
			verify_token(input.refresh_token)
		except jwt.exceptions.InvalidTokenError:
			raise GraphQLError("Invalid refresh token")

		user = await load_user_context(info.context.user)
		stored_refresh_token = user.refresh_token.decode() if user.refresh_token else None

		if not stored_refresh_token or stored_refresh_token != input.refresh_token:
			raise GraphQLError("Invalid refresh token")

		new_access_token = generate_token(user.username, {})
		new_refresh_token = generate_token(
			user.username, {}, expires_delta=datetime.timedelta(seconds=env.int("REFRESH_TOKEN_EXPIRES"))
		)

		await current_app.db.execute(
			'UPDATE "user" SET refresh_token = :refresh_token WHERE id = :user_id',
			values={"refresh_token": bytes(new_refresh_token.encode()), "user_id": user.id},
		)

		return AuthResponse(
			message="Token refresh successful",
			access_token=new_access_token,
			refresh_token=new_refresh_token,
			token_type="bearer",
			expires_in=env.int("ACCESS_TOKEN_EXPIRES"),
		)

	@strawberry.mutation
	@token_required
	async def logout(self, info: Info[Context, Any]) -> bool:
		user = await load_user_context(info.context.user)
		token_blacklist.add(user.jti)
		return True

	@strawberry.mutation
	@token_required
	async def create_topic(self, info: Info, input: TopicInput) -> Topic:
		user = await load_user_context(info.context.user)
		query = """
		INSERT INTO topic (topic, disabled, owner, modified_by)
		VALUES (:topic, :disabled, :owner, :modified_by)
		RETURNING id, topic, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"topic": str(input.topic),
			"disabled": bool(input.disabled),
			"owner": user.username,
			"modified_by": user.username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		await topic_signal.send_async("add_topic", topic=str(input.topic))
		return Topic(**row)

	@strawberry.mutation
	@token_required
	async def update_topic(self, info: Info, id: int, input: TopicInput) -> Topic:
		user = await load_user_context(info.context.user)
		query = """
		UPDATE topic
		SET topic = :topic,
			disabled = :disabled,
			modified = CURRENT_TIMESTAMP,
			modified_by = :modified_by
		WHERE id = :id
		RETURNING id, topic, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"id": id,
			"topic": input.topic,
			"disabled": input.disabled,
			"modified_by": user.username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		await topic_signal.send("add_topic", topic=str(input.topic))
		return Topic(**row)

	@strawberry.mutation
	@token_required
	async def create_user(self, info: Info, input: UserInput) -> User:
		user = await load_user_context(info.context.user)
		env = Env()
		f = Fernet(env.str("FERNET_KEY").encode())

		encrypted_password = f.encrypt(input.password.encode()) if input.password else ""
		query = """
		INSERT INTO "user" (username, password_hash, disabled, owner, modified_by)
		VALUES (:username, :password, :disabled, :owner, :modified_by)
		RETURNING id, username, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"username": input.username,
			"password": encrypted_password,
			"disabled": input.disabled,
			"owner": user.username,
			"modified_by": user.username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		return User(**row)

	@strawberry.mutation
	@token_required
	async def update_user(self, info: Info, id: int, input: UserInput) -> User:
		user = await load_user_context(info.context.user)
		query = """
		UPDATE "user"
		SET username = :username,
			password_hash = :password,
			disabled = :disabled,
			modified = CURRENT_TIMESTAMP,
			modified_by = :modified_by
		WHERE id = :id
		RETURNING id, username, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"id": id,
			"username": input.username,
			"password": Fernet.encrypt(input.password.encode()),
			"disabled": input.disabled,
			"modified_by": user.username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		return User(**row)

	@strawberry.mutation
	@token_required
	async def alarm(self, info: Info, input: AlarmInput) -> Alarm:
		user = await load_user_context(info.context.user)

		if input.id is None:
			query = """
				INSERT INTO alarm (
					condition, owner, modified_by, topic,
					alarm_name, delivery_method, disabled
				)
				VALUES (
					:condition, :owner, :modified_by, :topic,
					:alarm_name, :delivery_method, :disabled
				)
				RETURNING id, condition, owner, creation, modified, modified_by,
					disabled, topic, alarm_name, delivery_method
			"""
			values = {
				"condition": input.condition,
				"owner": input.owner,
				"modified_by": user.username,
				"topic": input.topic,
				"alarm_name": input.alarm_name,
				"delivery_method": input.delivery_method,
				"disabled": input.disabled,
			}
		else:
			# Update existing alarm
			query = """
				UPDATE alarm
				SET condition = :condition,
					owner = :owner,
					modified_by = :modified_by,
					topic = :topic,
					alarm_name = :alarm_name,
					delivery_method = :delivery_method,
					disabled = :disabled,
					modified = CURRENT_TIMESTAMP
				WHERE id = :id
				RETURNING id, condition, owner, creation, modified, modified_by,
									disabled, topic, alarm_name, delivery_method
			"""
			values = {
				"id": input.id,
				"condition": input.condition,
				"owner": input.owner,
				"modified_by": user.username,
				"topic": input.topic,
				"alarm_name": input.alarm_name,
				"delivery_method": input.delivery_method,
				"disabled": input.disabled,
			}

		row = await current_app.db.fetch_one(query=query, values=values)
		await alarm_signal.send("refresh_alarm")
		return Alarm(**dict(row))


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_app = GraphQL(schema)


@graphql_bp.route("/", methods=["GET", "POST"])
async def graphql_handler():
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

		return jsonify(
			{"data": result.data} if result.data else {"errors": [str(err) for err in result.errors]}
		)

	return jsonify({"errors": ["Invalid Content-Type"]}), 400
