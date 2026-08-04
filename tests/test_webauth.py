"""Who may reach the account page, in both deployment modes.

The page can view live video, switch cameras off and unlink the Xiaomi
account. These tests exist so that widening access to it fails the build rather
than shipping quietly.

Requests go through a real ``aiohttp`` application rather than a mocked request
object: the guards are middleware, and middleware that is never mounted is not
what runs in production.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bridge.webauth import (
    INGRESS_HEADER,
    SESSION_COOKIE,
    build_guards,
    session_token,
)

PASSWORD = "correct-horse-battery"


async def reached(request: web.Request) -> web.Response:
    """The endpoint a request only arrives at once the guards let it past."""
    return web.Response(text="reached")


async def make_client(**kwargs: object) -> TestClient:
    """An application carrying nothing but the guards and two endpoints.

    The test server listens on loopback, which the ingress guard accepts as a
    peer address -- Supervisor's own network is not reachable from a test.
    """
    kwargs.setdefault("published", True)
    app = web.Application(middlewares=build_guards(**kwargs))
    app.router.add_get("/", reached)
    app.router.add_get("/api/cameras", reached)
    app.router.add_post("/api/login", reached)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def session(password: str = PASSWORD) -> dict[str, str]:
    return {"Cookie": f"{SESSION_COOKIE}={session_token(password)}"}


class TestGuardSelection:
    def test_a_published_page_without_a_password_refuses_to_build(self) -> None:
        # The load-bearing case: reachable from the network, no Supervisor in
        # front and no password, so startup fails instead of serving it.
        with pytest.raises(ValueError, match="nothing guarding"):
            build_guards(supervised=False, published=True, web_password="")

    def test_a_loopback_page_needs_no_password(self) -> None:
        # Same protection the control plane has relied on all along: nothing
        # off the host can open the connection.
        assert build_guards(supervised=False, published=False, web_password="") == []

    def test_supervised_without_password_is_fine(self) -> None:
        assert build_guards(supervised=True, published=True, web_password="")


class TestIngress:
    async def test_a_request_without_the_header_is_refused(self) -> None:
        client = await make_client(supervised=True, web_password="")
        try:
            assert (await client.get("/api/cameras")).status == 403
        finally:
            await client.close()

    async def test_a_request_through_ingress_is_allowed(self) -> None:
        client = await make_client(supervised=True, web_password="")
        try:
            response = await client.get("/api/cameras", headers={INGRESS_HEADER: ""})
            assert response.status == 200
        finally:
            await client.close()


class TestPassword:
    """Set means checked, whatever the deployment.

    Accepting either credential meant a password configured on an add-on was
    never asked for -- ingress vouched for every panel request on its own, so
    the setting silently did nothing while the user believed they had one.
    """

    async def test_ingress_alone_is_not_enough(self) -> None:
        client = await make_client(supervised=True, web_password=PASSWORD)
        try:
            response = await client.get("/api/cameras", headers={INGRESS_HEADER: ""})
            assert response.status == 401
        finally:
            await client.close()

    async def test_both_together_are_accepted(self) -> None:
        client = await make_client(supervised=True, web_password=PASSWORD)
        try:
            response = await client.get(
                "/api/cameras", headers={INGRESS_HEADER: "", **session()}
            )
            assert response.status == 200
        finally:
            await client.close()

    async def test_a_wrong_session_is_refused(self) -> None:
        client = await make_client(supervised=True, web_password=PASSWORD)
        try:
            response = await client.get(
                "/api/cameras",
                headers={INGRESS_HEADER: "", **session("something else")},
            )
            assert response.status == 401
        finally:
            await client.close()

    async def test_the_session_alone_is_not_enough_on_an_addon(self) -> None:
        # Ingress still has to have forwarded it: the password is an addition
        # to that check, not a way around it.
        client = await make_client(supervised=True, web_password=PASSWORD)
        try:
            assert (await client.get("/api/cameras", headers=session())).status == 403
        finally:
            await client.close()

    async def test_standalone_needs_only_the_session(self) -> None:
        client = await make_client(supervised=False, web_password=PASSWORD)
        try:
            assert (await client.get("/api/cameras", headers=session())).status == 200
            assert (await client.get("/api/cameras")).status == 401
        finally:
            await client.close()


class TestWayIn:
    """Signing in has to be reachable without being signed in.

    Guarding the page and the endpoint that unlocks it would leave a viewer
    with a 401 and nowhere to type the password.
    """

    async def test_the_page_itself_is_reachable(self) -> None:
        client = await make_client(supervised=False, web_password=PASSWORD)
        try:
            assert (await client.get("/")).status == 200
        finally:
            await client.close()

    async def test_the_login_endpoint_is_reachable(self) -> None:
        client = await make_client(supervised=False, web_password=PASSWORD)
        try:
            assert (await client.post("/api/login")).status == 200
        finally:
            await client.close()

    async def test_nothing_else_is(self) -> None:
        client = await make_client(supervised=False, web_password=PASSWORD)
        try:
            assert (await client.get("/api/cameras")).status == 401
        finally:
            await client.close()


class TestSessionToken:
    def test_it_is_not_the_password(self) -> None:
        # Cookies are written to disk and shown by browser tooling.
        assert PASSWORD not in session_token(PASSWORD)

    def test_it_survives_a_restart(self) -> None:
        # Derived rather than random, so an update does not sign everyone out.
        assert session_token(PASSWORD) == session_token(PASSWORD)

    def test_different_passwords_differ(self) -> None:
        assert session_token(PASSWORD) != session_token(PASSWORD + "!")

    def test_a_non_ascii_password_works(self) -> None:
        # `compare_digest` refuses non-ASCII strings, so the comparison is on
        # the hex digest rather than on the password itself.
        assert session_token("密码-一二三")


class TestForgedIngressHeader:
    """The ingress header must never decide what a path means.

    It shipped doing exactly that: the guard stripped a prefix taken from the
    header before deciding whether a path was public, so a long enough forged
    header left an empty path, which read as the root and let everything
    through. Standalone, where that guard is the only one, it was a complete
    bypass of the password.
    """

    async def test_a_long_forged_header_does_not_open_anything(self) -> None:
        client = await make_client(supervised=False, web_password=PASSWORD)
        try:
            for forged in ["/" * 64, "/api/hassio_ingress/" + "x" * 40, "/api/cameras"]:
                response = await client.get(
                    "/api/cameras", headers={INGRESS_HEADER: forged}
                )
                assert response.status == 401, forged
        finally:
            await client.close()

    async def test_it_does_not_open_anything_on_an_addon_either(self) -> None:
        client = await make_client(supervised=True, web_password=PASSWORD)
        try:
            response = await client.get(
                "/api/cameras", headers={INGRESS_HEADER: "/" * 64}
            )
            # Past the ingress check, which only wants the header present, and
            # stopped by the password.
            assert response.status == 401
        finally:
            await client.close()
