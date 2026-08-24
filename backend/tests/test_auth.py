"""Registration, sign-in, and the refresh-token lifecycle.

The interesting half of this file is not "can a student log in" -- it is the
three properties the auth router's docstring claims and that nothing else in the
suite would notice losing:

* refresh **rotates**, so a stolen token is usable at most once;
* logout **revokes**, so "signed out" is not merely a cleared localStorage;
* login is not an **oracle** -- an unknown address and a wrong password produce
  byte-identical responses, or the endpoint enumerates who has an account.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.core.config import settings

PASSWORD = "correct-horse-battery"


async def _register(
    client: AsyncClient, campus: dict[str, str], email: str = "amina@university.edu"
) -> dict[str, Any]:
    response = await client.post(
        "/auth/register",
        json={
            "name": "Amina Rahman",
            "email": email,
            "password": PASSWORD,
            "campus_id": campus["campus_id"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_register_returns_a_usable_token_pair(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    tokens = await _register(client, campus)

    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == settings.access_token_minutes * 60
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["access_token"] != tokens["refresh_token"]

    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200, me.text


async def test_register_rejects_a_duplicate_email(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    await _register(client, campus, email="duplicate@university.edu")

    # Same address, different case: emails are lowercased before the UNIQUE
    # index sees them, so this is the same account.
    second = await client.post(
        "/auth/register",
        json={
            "name": "Someone Else",
            "email": "Duplicate@University.edu",
            "password": PASSWORD,
            "campus_id": campus["campus_id"],
        },
    )
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


async def test_register_rejects_an_unknown_campus(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "name": "Nowhere Student",
            "email": "nowhere@university.edu",
            "password": PASSWORD,
            "campus_id": "not-a-campus",
        },
    )
    assert response.status_code == 404


async def test_login_returns_a_new_pair(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    registered = await _register(client, campus)

    response = await client.post(
        "/auth/login",
        json={"email": "amina@university.edu", "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    assert response.json()["refresh_token"] != registered["refresh_token"]


async def test_login_is_not_an_account_oracle(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    await _register(client, campus)

    wrong_password = await client.post(
        "/auth/login",
        json={"email": "amina@university.edu", "password": "not-the-password"},
    )
    unknown_account = await client.post(
        "/auth/login",
        json={"email": "nobody@university.edu", "password": PASSWORD},
    )

    assert wrong_password.status_code == 401
    assert unknown_account.status_code == 401
    # Identical, deliberately: any difference here tells an attacker which
    # campus addresses are registered.
    assert wrong_password.json() == unknown_account.json()


async def test_refresh_rotates_and_burns_the_old_handle(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    tokens = await _register(client, campus)

    rotated = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()
    assert fresh["refresh_token"] != tokens["refresh_token"]

    # The replacement works...
    assert (
        await client.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    ).status_code == 200

    # ...and the handle it replaced is dead, however valid its signature is.
    replayed = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replayed.status_code == 401


async def test_access_token_is_not_accepted_as_a_refresh_token(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    tokens = await _register(client, campus)

    # The ``kind`` claim is what stops a 30-minute session silently becoming a
    # 14-day one.
    response = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["access_token"]}
    )
    assert response.status_code == 401


async def test_logout_revokes_the_refresh_token(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    tokens = await _register(client, campus)

    signed_out = await client.post(
        "/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert signed_out.status_code == 200

    after = await client.post(
        "/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert after.status_code == 401

    # Logging out twice is not an error -- a client retrying after a dropped
    # response must not see a failure.
    assert (
        await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 200


async def test_users_me_describes_the_caller(
    client: AsyncClient, campus: dict[str, str]
) -> None:
    tokens = await _register(client, campus)

    response = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["email"] == "amina@university.edu"
    assert body["name"] == "Amina Rahman"
    assert body["campus_id"] == campus["campus_id"]
    assert body["campus_name"] == campus["name"]
    assert body["role"] == "STUDENT"
    assert body["rating_count"] == 0
    assert body["upload_count"] == 0
    # The whole point of a schema separate from the table.
    assert "password_hash" not in body


async def test_users_me_requires_a_token(client: AsyncClient) -> None:
    assert (await client.get("/users/me")).status_code == 401
