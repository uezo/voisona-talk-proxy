import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Optional
import aiofiles
import httpx


logger = logging.getLogger(__name__)


def raise_for_status_with_body(response: httpx.Response):
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        message = f"{exc}\nResponse body:\n{response.text}"
        raise httpx.HTTPStatusError(
            message,
            request=exc.request,
            response=exc.response,
        ) from exc


class VoisonaTalkClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        cache_dir: Optional[str] = None,
        timeout: float = 30.0,
        file_poll_interval: float = 0.02,
        file_stable_checks: int = 2,
        delete_request: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.cache_dir = str(
            Path(cache_dir or os.getenv("VOISONA_CACHE_DIR", "voisona_talk_cache")).resolve()
        )
        self.timeout = timeout
        self.file_poll_interval = file_poll_interval
        self.file_stable_checks = file_stable_checks
        self.delete_request = delete_request
        self.http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        os.makedirs(self.cache_dir, exist_ok=True)

    @property
    def auth(self):
        if self.username is None and self.password is None:
            return None
        return (self.username or "", self.password or "")

    async def close(self):
        await self.http_client.aclose()

    async def get_voices(self):
        response = await self.http_client.get(f"{self.base_url}/voices", auth=self.auth)
        raise_for_status_with_body(response)
        return response.json()

    def make_cache_key(self, payload: dict) -> str:
        normalized_payload = dict(payload)
        normalized_payload.pop("output_file_path", None)
        normalized_payload.pop("destination", None)
        normalized_payload.pop("force_enqueue", None)
        normalized_payload.pop("can_overwrite_file", None)
        encoded = json.dumps(
            normalized_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def make_cache_path(self, payload: dict) -> str:
        cache_key = self.make_cache_key(payload)
        cache_dir = self.make_voice_cache_dir(payload.get("voice_name"))
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"{cache_key}.wav")

    @staticmethod
    def make_cache_dir_name(voice_name: Optional[str]) -> str:
        if not voice_name:
            return "default"
        return str(voice_name).replace("/", "_").replace("\\", "_")

    def make_voice_cache_dir(self, voice_name: Optional[str]) -> str:
        cache_root = Path(self.cache_dir).resolve()
        cache_dir = (cache_root / self.make_cache_dir_name(voice_name)).resolve()
        if cache_dir == cache_root or cache_root not in cache_dir.parents:
            raise ValueError("Cache path escapes the cache directory")
        return str(cache_dir)

    def clear_cache(self, voice_name: Optional[str] = None) -> bool:
        cache_dir = self.make_voice_cache_dir(voice_name)
        if not os.path.isdir(cache_dir):
            return False
        shutil.rmtree(cache_dir)
        return True

    async def read_cached_audio(self, cache_path: str) -> Optional[bytes]:
        try:
            size = os.path.getsize(cache_path)
        except FileNotFoundError:
            return None

        return await self.read_complete_wav(cache_path, size)

    async def synthesize(self, payload: dict) -> bytes:
        cache_path = self.make_cache_path(payload)

        cached_audio = await self.read_cached_audio(cache_path)
        if cached_audio is not None:
            return cached_audio

        request_uuid = None
        try:
            upstream_payload = dict(payload)
            upstream_payload.pop("output_file_path", None)
            upstream_payload.pop("destination", None)
            upstream_payload.setdefault("force_enqueue", True)
            upstream_payload["can_overwrite_file"] = True
            upstream_payload["destination"] = "file"
            upstream_payload["output_file_path"] = cache_path

            response = await self.http_client.post(
                f"{self.base_url}/speech-syntheses",
                auth=self.auth,
                json=upstream_payload,
            )
            raise_for_status_with_body(response)
            request_uuid = response.json().get("uuid")

            audio = await self.wait_and_read_file(cache_path)
            if not audio:
                raise RuntimeError("Voisona generated an empty audio file")
            return audio
        finally:
            if request_uuid and self.delete_request:
                try:
                    await self.delete_synthesis_request(request_uuid)
                except Exception:
                    logger.exception("Failed to delete Voisona speech synthesis request")

    async def wait_and_read_file(self, output_file_path: str) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        last_size = -1
        stable_count = 0

        while loop.time() < deadline:
            try:
                size = os.path.getsize(output_file_path)
            except FileNotFoundError:
                size = -1

            audio = await self.read_complete_wav(output_file_path, size)
            if audio is not None:
                return audio

            if size > 0 and size == last_size:
                stable_count += 1
                if stable_count >= self.file_stable_checks:
                    async with aiofiles.open(output_file_path, "rb") as f:
                        return await f.read()
            else:
                stable_count = 0
                last_size = size

            await asyncio.sleep(self.file_poll_interval)

        raise TimeoutError("Timed out waiting for Voisona output file")

    @staticmethod
    async def read_complete_wav(output_file_path: str, size: int) -> Optional[bytes]:
        if size < 44:
            return None

        async with aiofiles.open(output_file_path, "rb") as f:
            header = await f.read(8)
            if len(header) < 8 or header[:4] != b"RIFF":
                return None

            expected_size = int.from_bytes(header[4:8], "little") + 8
            if size < expected_size:
                return None

            await f.seek(0)
            audio = await f.read()
            if len(audio) < expected_size:
                return None
            return audio

    async def delete_synthesis_request(self, request_uuid: str):
        response = await self.http_client.delete(
            f"{self.base_url}/speech-syntheses/{request_uuid}",
            auth=self.auth,
        )
        raise_for_status_with_body(response)
