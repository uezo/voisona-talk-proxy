import asyncio
import sys

import httpx

from voisona_talk_proxy import cli
from voisona_talk_proxy.cli import create_app, parse_args


def test_cli_default_port_is_proxy_port(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["voisona-talk-proxy"])

    args = parse_args()

    assert args.port == 32777
    assert args.log_level is None


def test_cli_uses_log_level_from_environment(monkeypatch):
    run_kwargs = {}

    monkeypatch.setattr(sys, "argv", ["voisona-talk-proxy"])
    monkeypatch.setenv("VOISONA_LOG_LEVEL", "debug")
    monkeypatch.setattr(cli, "configure_logging", lambda log_level: None)
    monkeypatch.setattr(cli, "create_app", lambda **kwargs: object())

    def fake_run(app, **kwargs):
        run_kwargs.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.main()

    assert run_kwargs["log_level"] == "debug"


def test_create_app_closes_proxy_on_lifespan_shutdown(monkeypatch):
    closed = False

    class FakeProxy:
        def __init__(self, **kwargs):
            pass

        def get_api_router(self):
            from fastapi import APIRouter

            return APIRouter()

        async def close(self):
            nonlocal closed
            closed = True

    async def scenario():
        monkeypatch.setattr(cli, "VoisonaProxy", FakeProxy)

        app = create_app()

        async with app.router.lifespan_context(app):
            assert closed is False

        assert closed is True

    asyncio.run(scenario())


def test_create_app_serves_playground(monkeypatch):
    class FakeProxy:
        def __init__(self, **kwargs):
            pass

        def get_api_router(self):
            from fastapi import APIRouter

            return APIRouter()

        async def close(self):
            pass

    async def scenario():
        monkeypatch.setattr(cli, "VoisonaProxy", FakeProxy)
        app = create_app()
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            index_response = await client.get("/playground/")
            script_response = await client.get("/playground/app.js")

        assert index_response.status_code == 200, index_response.text
        assert "VoiSona Talk Proxy" in index_response.text
        assert script_response.status_code == 200, script_response.text
        assert "URL.createObjectURL" in script_response.text

    asyncio.run(scenario())
