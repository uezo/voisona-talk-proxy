import asyncio
import base64
import configparser
import json
from pathlib import Path
import struct

import httpx
import pytest

from voisona_talk_proxy.client import VoisonaTalkClient


def load_voisona_config():
    config = configparser.ConfigParser()
    config.read("pytest.ini")
    section = config["voisona"]
    return {
        "base_url": section["BASE_URL"],
        "username": section.get("VOISONA_USERNAME"),
        "password": section.get("VOISONA_PASSWORD"),
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


def get_client(cache_dir: Path):
    config = load_voisona_config()
    return VoisonaTalkClient(
        base_url=config["base_url"],
        username=config["username"],
        password=config["password"],
        cache_dir=str(cache_dir),
        timeout=60.0,
        delete_request=True,
    )


def run(coro):
    return asyncio.run(coro)


def basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def make_wav(data: bytes = b"\x00\x00\x01\x00") -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 48000, 96000, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def test_get_voices_can_override_client_credentials():
    async def scenario():
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"items": []})

        client = VoisonaTalkClient(
            base_url="http://test",
            username="self-user",
            password="self-pass",
        )
        await client.http_client.aclose()
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        try:
            assert await client.get_voices(username="request-user", password="request-pass") == {
                "items": []
            }
        finally:
            await client.close()

        assert requests[0].headers["authorization"] == basic_auth_header(
            "request-user",
            "request-pass",
        )

    run(scenario())


def test_synthesize_uses_method_credentials_without_forwarding_body_credentials(tmp_path):
    async def scenario():
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"uuid": "request-id"})
            return httpx.Response(204)

        client = VoisonaTalkClient(
            base_url="http://test",
            username="self-user",
            password="self-pass",
            cache_dir=str(tmp_path),
            delete_request=True,
        )
        await client.http_client.aclose()
        client.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def read_cached_audio(_cache_path):
            return None

        async def wait_and_read_file(_cache_path):
            return b"RIFF" + (b"\x00" * 40)

        client.read_cached_audio = read_cached_audio
        client.wait_and_read_file = wait_and_read_file

        try:
            audio = await client.synthesize(
                {
                    "text": "hello",
                    "voice_name": "voice-a",
                    "username": "body-user",
                    "password": "body-pass",
                    "destination": "memory",
                    "output_file_path": "/tmp/user-requested.wav",
                    "force_enqueue": False,
                    "can_overwrite_file": False,
                },
                username="request-user",
                password="request-pass",
            )
        finally:
            await client.close()

        assert audio.startswith(b"RIFF")
        assert [request.method for request in requests] == ["POST", "DELETE"]
        assert all(
            request.headers["authorization"] == basic_auth_header("request-user", "request-pass")
            for request in requests
        )

        upstream_payload = json.loads(requests[0].content)
        assert "username" not in upstream_payload
        assert "password" not in upstream_payload
        assert upstream_payload["text"] == "hello"
        assert upstream_payload["destination"] == "file"
        assert upstream_payload["output_file_path"] != "/tmp/user-requested.wav"
        assert upstream_payload["force_enqueue"] is True
        assert upstream_payload["can_overwrite_file"] is True

    run(scenario())


def test_read_complete_wav_uses_data_chunk_and_returns_original_riff_size(tmp_path):
    async def scenario():
        wav_path = tmp_path / "audio.wav"
        wav = bytearray(make_wav())
        wav[4:8] = (len(wav) + 60 - 8).to_bytes(4, "little")
        wav_path.write_bytes(wav)

        audio = await VoisonaTalkClient.read_complete_wav(str(wav_path), wav_path.stat().st_size)

        assert audio is not None
        assert len(audio) == len(wav)
        assert int.from_bytes(audio[4:8], "little") + 8 == len(audio) + 60
        assert int.from_bytes(wav_path.read_bytes()[4:8], "little") + 8 == len(audio) + 60

    run(scenario())


def test_get_voices_returns_installed_voices(tmp_path):
    async def scenario():
        client = get_client(tmp_path)
        try:
            voices = await client.get_voices()
            voice = get_first_voice(voices)
            assert isinstance(voice["voice_name"], str)
        finally:
            await client.close()

    run(scenario())


def test_synthesize_writes_voice_cache_and_returns_wav(tmp_path):
    async def scenario():
        client = get_client(tmp_path)
        try:
            voice = get_first_voice(await client.get_voices())
            payload = {
                "text": "pytest integration test",
                "language": voice["languages"][0],
                "voice_name": voice["voice_name"],
                "voice_version": voice["voice_version"],
            }

            audio = await client.synthesize(payload)
            assert_wav(audio)

            cache_path = Path(client.make_cache_path(payload))
            assert cache_path.exists()
            assert cache_path.parent.name == voice["voice_name"]
            assert cache_path.read_bytes() == audio

            cached_audio = await client.synthesize(payload)
            assert cached_audio == audio
        finally:
            await client.close()

    run(scenario())


def test_clear_cache_removes_only_selected_voice_cache(tmp_path):
    async def scenario():
        client = get_client(tmp_path)
        try:
            first_payload = {"text": "a", "voice_name": "voice-a"}
            second_payload = {"text": "b", "voice_name": "voice-b"}

            first_cache = Path(client.make_cache_path(first_payload))
            second_cache = Path(client.make_cache_path(second_payload))
            first_cache.write_bytes(b"cache-a")
            second_cache.write_bytes(b"cache-b")

            assert client.clear_cache("voice-a") is True
            assert not first_cache.parent.exists()
            assert second_cache.parent.exists()
            assert client.clear_cache("missing") is False
        finally:
            await client.close()

    run(scenario())


def test_cache_voice_name_cannot_escape_cache_dir(tmp_path):
    async def scenario():
        client = get_client(tmp_path / "cache")
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        try:
            with pytest.raises(ValueError):
                client.make_cache_path({"text": "x", "voice_name": ".."})

            with pytest.raises(ValueError):
                client.clear_cache("..")

            with pytest.raises(ValueError):
                client.clear_cache(".")

            assert outside_dir.exists()
        finally:
            await client.close()

    run(scenario())
