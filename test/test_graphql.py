import pytest


@pytest.mark.asyncio
async def test_successful_login(test_client, login_mutation, execute_graphql) -> None:
	response = await execute_graphql(login_mutation)
	assert "data" in response
	assert "login" in response["data"]
	assert response["data"]["login"]["message"] == "Login successful"
	assert "accessToken" in response["data"]["login"]
	assert "refreshToken" in response["data"]["login"]
	assert response["data"]["login"]["tokenType"] == "bearer"
	assert response["data"]["login"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_login(test_client, execute_graphql) -> None:
	bad_login_mutation = """
        mutation {
            login(input: { username: "admin", password: "wrongpassword" }) {
                message
                accessToken
            }
        }
    """
	response = await execute_graphql(bad_login_mutation)
	assert "errors" in response
	assert "Invalid credentials" in response["errors"][0]


@pytest.mark.asyncio
async def test_create_topic(test_client, login_mutation, topic_mutation, execute_graphql) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	response = await execute_graphql(topic_mutation, token=token)
	assert "data" in response
	assert len(response["data"]["createTopic"]) > 0


@pytest.mark.asyncio
async def test_topics_query_with_valid_token(
	test_client, login_mutation, topics_query, execute_graphql
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	response = await execute_graphql(topics_query, token=token)
	assert "data" in response
	assert "getTopics" in response["data"]
	assert len(response["data"]["getTopics"]) > 0


@pytest.mark.asyncio
async def test_topics_query_with_invalid_token(test_client, topics_query, execute_graphql) -> None:
	response = await execute_graphql(topics_query, token="invalid_token")
	assert "errors" in response
	assert "Authorization required" in response["errors"][0]


@pytest.mark.asyncio
async def test_successful_logout(
	test_client, login_mutation, logout_mutation, topics_query, execute_graphql
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	logout_response = await execute_graphql(logout_mutation, token=token)
	assert "data" in logout_response
	assert logout_response["data"]["logout"] is True

	topics_response = await execute_graphql(topics_query, token=token)
	assert "errors" in topics_response
	assert "Authorization required" in topics_response["errors"][0]


@pytest.mark.asyncio
async def test_using_token_after_logout(
	test_client, login_mutation, logout_mutation, topics_query, execute_graphql
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]

	topics_response = await execute_graphql(topics_query, token=token)
	assert "data" in topics_response
	assert len(topics_response["data"]["getTopics"]) > 0

	await execute_graphql(logout_mutation, token=token)

	topics_response_after_logout = await execute_graphql(topics_query, token=token)
	assert "errors" in topics_response_after_logout
	assert "Authorization required" in topics_response_after_logout["errors"][0]


@pytest.mark.asyncio
async def test_successful_token_refresh(
	test_client, login_mutation, refresh_token_mutation, execute_graphql
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	refresh_token = login_response["data"]["login"]["refreshToken"]

	refresh_response = await execute_graphql(
		refresh_token_mutation,
		token=token,
		variables={"refresh_token": refresh_token},
	)
	assert "data" in refresh_response
	assert "refreshToken" in refresh_response["data"]
	assert refresh_response["data"]["refreshToken"]["message"] == "Token refresh successful"
	assert "accessToken" in refresh_response["data"]["refreshToken"]
	assert "refreshToken" in refresh_response["data"]["refreshToken"]
	assert refresh_response["data"]["refreshToken"]["tokenType"] == "bearer"
	assert refresh_response["data"]["refreshToken"]["expiresIn"] > 0


@pytest.mark.asyncio
async def test_failed_token_refresh(
	test_client, refresh_token_mutation, login_mutation, execute_graphql
) -> None:
	login_response = await execute_graphql(login_mutation)
	token = login_response["data"]["login"]["accessToken"]
	refresh_token = login_response["data"]["login"]["refreshToken"]

	response = await execute_graphql(
		refresh_token_mutation,
		token=token,
		variables={"refresh_token": f"{refresh_token}invalidCharacters"},
	)
	assert "errors" in response
	assert "Invalid refresh token" in response["errors"][0]
