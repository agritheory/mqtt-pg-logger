from quart import Quart, render_template_string, websocket
from quart_auth import AuthUser, current_user, login_required, login_user, logout_user, QuartAuth

app = Quart(__name__)
app.secret_key = "secret key"  # Do not use this key

QuartAuth(app)


@app.route("/login")
async def login():
	# Check Credentials here, e.g. username & password.
	...
	# We'll assume the user has an identifying ID equal to 2
	login_user(AuthUser(2))
	...


@app.route("/logout")
async def logout():
	logout_user()
	...


@app.route("/")
@login_required
async def restricted_route():
	current_user.auth_id  # Will be 2 given the login_user code above
	...


@app.route("/hello")
async def hello():
	return await render_template_string(
		"""
    {% if current_user.is_authenticated %}
      Hello logged in user
    {% else %}
      Hello logged out user
    {% endif %}
    """
	)


@app.websocket("/ws")
@login_required
async def ws():
	await websocket.send(f"Hello {current_user.auth_id}")
	...
