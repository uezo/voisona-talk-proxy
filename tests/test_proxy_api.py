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
    }


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
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{config['api_base_url']}/voices")

        assert_ok(response)
        get_first_voice(response.json())

    run(scenario())


def test_proxy_synthesize_returns_wav():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0) as client:
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


def test_proxy_compat_paths():
    async def scenario():
        config = load_api_config()
        async with httpx.AsyncClient(timeout=60.0) as client:
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
