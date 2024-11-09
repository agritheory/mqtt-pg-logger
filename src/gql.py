import datetime
from dataclasses import dataclass
from typing import Any

import strawberry
from cryptography.fernet import Fernet
from environs import Env
from quart import Blueprint, Response, current_app, jsonify, request
from strawberry.asgi import GraphQL
from strawberry.types import Info

from src.auth import generate_token, token_blacklist, verify_token

graphql_bp = Blueprint("graphql", __name__)


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


# Type definitions
@strawberry.type
class User:
	id: int
	username: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner: str
	modified_by: str


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


@strawberry.type
class Topic:
	id: int
	topic: str
	disabled: bool
	creation: datetime.datetime
	modified: datetime.datetime
	owner: str
	modified_by: str


# Input types
@strawberry.input
class TopicInput:
	topic: str
	disabled: bool = False


@strawberry.input
class UserInput:
	username: str
	password: str
	disabled: bool = False


# Queries
@strawberry.type
class Query:
	@strawberry.field
	async def get_topics(self, info: Info[Context, Any]) -> list[Topic]:
		if not info.context.user:
			raise Exception("Not authenticated")

		query = """
			SELECT id, topic, disabled, creation, modified, owner, modified_by
			FROM topic
			WHERE disabled = false
			ORDER BY topic
		"""

		rows = await current_app.db.fetch_all(query=query)
		return [Topic(**row) for row in rows]

	@strawberry.field
	async def get_topic(self, info: Info, topic_id: int) -> Topic | None:
		if not info.context.user:
			raise Exception("Not authenticated")
		query = """
		SELECT id, topic, disabled, creation, modified, owner, modified_by
		FROM topic
		WHERE id = :topic_id
		"""
		row = await current_app.db.fetch_one(query=query, values={"topic_id": topic_id})
		return Topic(**row) if row else None

	@strawberry.field
	async def get_users(self, info: Info) -> list[User]:
		if not info.context.user:
			raise Exception("Not authenticated")
		query = """
		SELECT id, username, disabled, creation, modified, owner, modified_by
		FROM "user"
		WHERE disabled = false
		ORDER BY username
		"""
		rows = await current_app.db.fetch_all(query=query)
		return [User(**row) for row in rows]

	@strawberry.field
	async def get_user(self, info: Info, user_id: int) -> User | None:
		if not info.context.user:
			raise Exception("Not authenticated")
		query = """
		SELECT id, username, disabled, creation, modified, owner, modified_by
		FROM "user"
		WHERE id = :user_id
		"""
		row = await current_app.db.fetch_one(query=query, values={"user_id": user_id})
		return User(**row) if row else None


# Mutations
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
			raise Exception("Invalid credentials")

		if user["disabled"]:
			raise Exception("Account is disabled")

		env = Env()
		f = Fernet(env.str("FERNET_KEY").encode())

		stored_hash = bytes(user["password_hash"])
		try:
			decrypted_password = f.decrypt(stored_hash).decode()
			if decrypted_password != input.password:
				raise Exception("Invalid credentials")
		except Exception:
			raise Exception("Invalid credentials")

		# Generate tokens
		access_token = generate_token(user["username"], {})
		refresh_token = generate_token(
			user["username"], {}, expires_delta=datetime.timedelta(seconds=env.int("REFRESH_TOKEN_EXPIRES"))
		)

		return AuthResponse(
			message="Login successful",
			access_token=access_token,
			refresh_token=refresh_token,
			token_type="bearer",
			expires_in=env.int("ACCESS_TOKEN_EXPIRES"),
		)

	@strawberry.mutation
	async def logout(self, info: Info[Context, Any]) -> bool:
		if not info.context.user:
			raise Exception("Not authenticated")
		token_blacklist.add(info.context.user["jti"])
		return True

	@strawberry.mutation
	async def create_topic(self, info: Info, input: TopicInput) -> Topic:
		if not info.context.user:
			raise Exception("Not authenticated")
		username = info.context.user.username
		query = """
		INSERT INTO topic (topic, disabled, owner, modified_by)
		VALUES (:topic, :disabled, :owner, :modified_by)
		RETURNING id, topic, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"topic": input.topic,
			"disabled": input.disabled,
			"owner": username,
			"modified_by": username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		return Topic(**row)

	@strawberry.mutation
	async def update_topic(self, info: Info, id: int, input: TopicInput) -> Topic:
		if not info.context.user:
			raise Exception("Not authenticated")
		username = info.context.user.username
		query = """
		UPDATE topic
		SET topic = :topic,
			disabled = :disabled,
			modified = CURRENT_TIMESTAMP,
			modified_by = :modified_by
		WHERE id = :id
		RETURNING id, topic, disabled, creation, modified, owner, modified_by
		"""
		values = {"id": id, "topic": input.topic, "disabled": input.disabled, "modified_by": username}
		row = await current_app.db.fetch_one(query=query, values=values)
		return Topic(**row)

	@strawberry.mutation
	async def create_user(self, info: Info, input: UserInput) -> User:
		if not info.context.user:
			raise Exception("Not authenticated")
		username = info.context.user.username
		env = Env()
		f = Fernet(env.str("FERNET_KEY").encode())

		encrypted_password = f.encrypt(input.password.encode())
		query = """
		INSERT INTO "user" (username, password_hash, disabled, owner, modified_by)
		VALUES (:username, :password, :disabled, :owner, :modified_by)
		RETURNING id, username, disabled, creation, modified, owner, modified_by
		"""
		values = {
			"username": input.username,
			"password": encrypted_password,
			"disabled": input.disabled,
			"owner": username,
			"modified_by": username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		return User(**row)

	@strawberry.mutation
	async def update_user(self, info: Info, id: int, input: UserInput) -> User:
		if not info.context.user:
			raise Exception("Not authenticated")
		username = info.context.user.username

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
			"modified_by": username,
		}
		row = await current_app.db.fetch_one(query=query, values=values)
		return User(**row)


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
