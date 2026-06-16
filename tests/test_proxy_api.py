import asyncio
import configparser

from fastapi import FastAPI
import httpx

from voisona_talk_proxy.proxy import VoisonaProxy


def load_api_config():
    config = configparser.ConfigParser()
    config.read("pytest.ini")
    section = config["voisona"]
    return {
        "api_base_url": section["API_BASE_URL"].rstrip("/"),
        "username": section.get("VOISONA_USERNAME"),
        "password": section.get("VOISONA_PASSWORD"),
    }


def get_api_auth(config):
    if config["username"] is None and config["password"] is None:
        return None
    return (config["username"] or "", config["password"] or "")


def get_first_voice(voices):
    items = voices["items"]
    assert items

    voice = items[0]
    assert voice["voice_name"]
    assert voice["voice_version"]
    assert voice["languages"]
    return voice


def assert_wav(audio: bytes):
    assert len(audio) >= 44
    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"


def assert_ok(response: httpx.Response):
    assert response.status_code == 200, response.text


def run(coro):
    return asyncio.run(coro)


def test_proxy_health():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{config['api_base_url']}/health")

        assert_ok(response)
        assert response.json() == {"status": "ok"}

    run(scenario())


def test_proxy_get_voices():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0, auth=get_api_auth(config)) as client:
            response = await client.get(f"{config['api_base_url']}/voices")

        assert_ok(response)
        get_first_voice(response.json())

    run(scenario())


def test_proxy_synthesize_returns_wav():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0, auth=get_api_auth(config)) as client:
            voices_response = await client.get(f"{config['api_base_url']}/voices")
            assert_ok(voices_response)
            voice = get_first_voice(voices_response.json())

            payload = {
                "text": "pytest proxy integration test",
                "language": voice["languages"][0],
                "voice_name": voice["voice_name"],
                "voice_version": voice["voice_version"],
            }
            response = await client.post(
                f"{config['api_base_url']}/speech-syntheses",
                json=payload,
            )

        assert_ok(response)
        assert response.headers["content-type"].startswith("audio/wav")
        assert_wav(response.content)

    run(scenario())


def test_proxy_synthesize_ignores_body_credentials():
    async def scenario():
        app = FastAPI()
        proxy = VoisonaProxy()

        class FakeClient:
            def __init__(self):
                self.call = None

            async def close(self):
                pass

            async def synthesize(self, payload, username=None, password=None):
                self.call = {
                    "payload": payload,
                    "username": username,
                    "password": password,
                }
                return b"RIFF" + (b"\x00" * 40)

        fake_client = FakeClient()
        await proxy.client.close()
        proxy.client = fake_client
        app.include_router(proxy.get_api_router())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            auth=("header-user", "header-pass"),
        ) as client:
            response = await client.post(
                "/speech-syntheses",
                json={
                    "text": "hello",
                    "language": "ja_JP",
                    "username": "body-user",
                    "password": "body-pass",
                },
            )

        assert_ok(response)
        assert fake_client.call == {
            "payload": {
                "text": "hello",
                "language": "ja_JP",
            },
            "username": "header-user",
            "password": "header-pass",
        }

    run(scenario())


def test_proxy_passes_basic_auth_credentials_to_client():
    async def scenario():
        app = FastAPI()
        proxy = VoisonaProxy()

        class FakeClient:
            def __init__(self):
                self.calls = []

            async def close(self):
                pass

            async def get_voices(self, username=None, password=None):
                self.calls.append(("get_voices", username, password))
                return {"items": []}

            async def synthesize(self, payload, username=None, password=None):
                self.calls.append(("synthesize", username, password))
                return b"RIFF" + (b"\x00" * 40)

        fake_client = FakeClient()
        await proxy.client.close()
        proxy.client = fake_client
        app.include_router(proxy.get_api_router())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            auth=("header-user", "header-pass"),
        ) as client:
            voices_response = await client.get("/voices")
            synthesize_response = await client.post(
                "/speech-syntheses",
                json={"text": "hello", "language": "ja_JP"},
            )

        assert_ok(voices_response)
        assert_ok(synthesize_response)
        assert fake_client.calls == [
            ("get_voices", "header-user", "header-pass"),
            ("synthesize", "header-user", "header-pass"),
        ]

    run(scenario())


def test_proxy_openapi_includes_speech_synthesis_request_schema():
    app = FastAPI()
    proxy = VoisonaProxy()
    app.include_router(proxy.get_api_router())

    schema = app.openapi()

    request_body = schema["paths"]["/speech-syntheses"]["post"]["requestBody"]
    assert request_body["content"]["application/json"]["schema"]["$ref"].endswith(
        "/SpeechSynthesisRequest"
    )

    request_schema = schema["components"]["schemas"]["SpeechSynthesisRequest"]
    properties = request_schema["properties"]
    assert request_schema["required"] == ["language"]
    assert properties["language"]["type"] == "string"
    assert properties["text"]["anyOf"][0]["type"] == "string"
    assert request_schema["anyOf"][0]["required"] == ["analyzed_text"]
    assert request_schema["anyOf"][1]["required"] == ["text"]
    assert "username" not in properties
    assert "password" not in properties
    assert properties["global_parameters"]["anyOf"][0]["$ref"].endswith(
        "/SpeechSynthesisGlobalParameters"
    )
    response_content = schema["paths"]["/speech-syntheses"]["post"]["responses"]["200"]["content"]
    assert "application/json" not in response_content
    assert response_content["audio/wav"]["schema"] == {
        "type": "string",
        "format": "binary",
    }
    assert schema["components"]["securitySchemes"]["HTTPBasic"]["type"] == "http"
    assert schema["components"]["securitySchemes"]["HTTPBasic"]["scheme"] == "basic"

    run(proxy.close())


def test_proxy_synthesize_requires_text_or_analyzed_text():
    async def scenario():
        app = FastAPI()
        proxy = VoisonaProxy()
        app.include_router(proxy.get_api_router())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/speech-syntheses", json={"language": "ja_JP"})

        await proxy.close()

        assert response.status_code == 422, response.text

    run(scenario())


def test_proxy_compat_paths():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0, auth=get_api_auth(config)) as client:
            health_response = await client.get(f"{config['api_base_url']}/api/talk/v1/health")
            voices_response = await client.get(f"{config['api_base_url']}/api/talk/v1/voices")

        assert_ok(health_response)
        assert health_response.json() == {"status": "ok"}
        assert_ok(voices_response)
        get_first_voice(voices_response.json())

    run(scenario())


def test_proxy_clear_cache_endpoint(tmp_path):
    async def scenario():
        app = FastAPI()
        proxy = VoisonaProxy(cache_dir=str(tmp_path))
        app.include_router(proxy.get_api_router())

        voice_cache = tmp_path / "voice-a"
        default_cache = tmp_path / "default"
        voice_cache.mkdir()
        default_cache.mkdir()
        (voice_cache / "audio.wav").write_bytes(b"cache-a")
        (default_cache / "audio.wav").write_bytes(b"default-cache")

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            voice_response = await client.delete("/cache/voice-a")
            default_response = await client.delete("/cache")

        await proxy.close()

        assert_ok(voice_response)
        assert voice_response.json() == {"cleared": True, "voice_name": "voice-a"}
        assert not voice_cache.exists()

        assert_ok(default_response)
        assert default_response.json() == {"cleared": True, "voice_name": "default"}
        assert not default_cache.exists()

    run(scenario())


def test_proxy_clear_cache_rejects_escaped_voice_name(tmp_path):
    async def scenario():
        app = FastAPI()
        proxy = VoisonaProxy(cache_dir=str(tmp_path / "cache"))
        app.include_router(proxy.get_api_router())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete("/cache", params={"voice_name": ".."})

        await proxy.close()

        assert response.status_code == 400, response.text

    run(scenario())
