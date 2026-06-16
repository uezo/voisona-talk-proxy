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
        return self.make_auth()

    def make_auth(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        effective_username = self.username if username is None else username
        effective_password = self.password if password is None else password
        if effective_username is None and effective_password is None:
            return None
        return (effective_username or "", effective_password or "")

    async def close(self):
        await self.http_client.aclose()

    async def get_voices(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        response = await self.http_client.get(
            f"{self.base_url}/voices",
            auth=self.make_auth(username, password),
        )
        raise_for_status_with_body(response)
        return response.json()

    def make_cache_key(self, payload: dict) -> str:
        normalized_payload = dict(payload)
        normalized_payload.pop("output_file_path", None)
        normalized_payload.pop("destination", None)
        normalized_payload.pop("force_enqueue", None)
        normalized_payload.pop("can_overwrite_file", None)
        normalized_payload.pop("username", None)
        normalized_payload.pop("password", None)
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

        audio = await self.read_complete_wav(cache_path, size)
        if audio is None:
            return None

        return audio

    async def synthesize(
        self,
        payload: dict,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bytes:
        request_username = username
        request_password = password
        auth = self.make_auth(request_username, request_password)
        cache_path = self.make_cache_path(payload)

        cached_audio = await self.read_cached_audio(cache_path)
        if cached_audio is not None:
            return cached_audio

        request_uuid = None
        try:
            upstream_payload = dict(payload)
            upstream_payload.pop("output_file_path", None)
            upstream_payload.pop("destination", None)
            upstream_payload.pop("username", None)
            upstream_payload.pop("password", None)
            upstream_payload["force_enqueue"] = True
            upstream_payload["can_overwrite_file"] = True
            upstream_payload["destination"] = "file"
            upstream_payload["output_file_path"] = cache_path

            response = await self.http_client.post(
                f"{self.base_url}/speech-syntheses",
                auth=auth,
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
                    await self.delete_synthesis_request(
                        request_uuid,
                        username=request_username,
                        password=request_password,
                    )
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
        if size < 12:
            return None

        async with aiofiles.open(output_file_path, "rb") as f:
            header = await f.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return None

            data_end = None
            offset = 12

            while offset + 8 <= size:
                await f.seek(offset)
                chunk_header = await f.read(8)
                if len(chunk_header) < 8:
                    return None

                chunk_id = chunk_header[:4]
                chunk_size = int.from_bytes(chunk_header[4:8], "little")
                chunk_data_start = offset + 8
                chunk_data_end = chunk_data_start + chunk_size
                padded_chunk_end = chunk_data_end + (chunk_size % 2)

                if chunk_id == b"data":
                    data_end = chunk_data_end
                    break

                if padded_chunk_end <= offset:
                    return None
                offset = padded_chunk_end

            if data_end is None or size < data_end:
                return None

            await f.seek(0)
            audio = await f.read()
            if len(audio) < data_end:
                return None

        return audio

    async def delete_synthesis_request(
        self,
        request_uuid: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        response = await self.http_client.delete(
            f"{self.base_url}/speech-syntheses/{request_uuid}",
            auth=self.make_auth(username, password),
        )
        raise_for_status_with_body(response)
