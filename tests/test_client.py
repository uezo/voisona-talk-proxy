import asyncio
import configparser
from pathlib import Path

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
