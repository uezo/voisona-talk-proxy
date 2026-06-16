import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from .client import VoisonaTalkClient
from .schemas import SpeechSynthesisRequest


logger = logging.getLogger(__name__)
optional_basic_auth = HTTPBasic(auto_error=False)
SPEECH_SYNTHESIS_RESPONSES = {
    200: {
        "description": "Synthesized WAV audio",
        "content": {
            "audio/wav": {
                "schema": {
                    "type": "string",
                    "format": "binary",
                }
            }
        },
    }
}


def auth_credentials(
    credentials: Optional[HTTPBasicCredentials] = Depends(optional_basic_auth),
) -> tuple[Optional[str], Optional[str]]:
    if credentials is None:
        return None, None
    return credentials.username, credentials.password


class VoisonaProxy:
    def __init__(
        self,
        *,
        voisona_url: str = "http://127.0.0.1:32766/api/talk/v1",
        username: str = None,
        password: str = None,
        cache_dir: str = None,
        timeout: float = 30.0,
        file_poll_interval: float = 0.02,
        file_stable_checks: int = 2,
        delete_request: bool = False,
    ):
        self.client = VoisonaTalkClient(
            base_url=voisona_url,
            username=username,
            password=password,
            cache_dir=cache_dir,
            timeout=timeout,
            file_poll_interval=file_poll_interval,
            file_stable_checks=file_stable_checks,
            delete_request=delete_request,
        )

    async def close(self):
        await self.client.close()

    def get_api_router(self, *, include_compat_paths: bool = True) -> APIRouter:
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok"}

        async def get_voices(
            credentials: tuple[Optional[str], Optional[str]] = Depends(auth_credentials),
        ):
            try:
                username, password = credentials
                return await self.client.get_voices(username=username, password=password)
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=str(exc))

        async def synthesize(
            request: SpeechSynthesisRequest,
            credentials: tuple[Optional[str], Optional[str]] = Depends(auth_credentials),
        ):
            try:
                payload = request.to_payload()
                payload.pop("username", None)
                payload.pop("password", None)
                header_username, header_password = credentials
                audio = await self.client.synthesize(
                    payload,
                    username=header_username,
                    password=header_password,
                )
                return Response(content=audio, media_type="audio/wav")
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=str(exc))
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail=str(exc))
            except Exception as exc:
                logger.exception("Voisona synthesis failed")
                raise HTTPException(status_code=500, detail=str(exc))

        async def clear_cache(voice_name: str = None):
            try:
                return {
                    "cleared": self.client.clear_cache(voice_name),
                    "voice_name": voice_name or "default",
                }
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        router.add_api_route("/voices", get_voices, methods=["GET"])
        router.add_api_route(
            "/speech-syntheses",
            synthesize,
            methods=["POST"],
            response_class=Response,
            responses=SPEECH_SYNTHESIS_RESPONSES,
        )
        router.add_api_route("/cache", clear_cache, methods=["DELETE"])
        router.add_api_route("/cache/{voice_name}", clear_cache, methods=["DELETE"])

        if include_compat_paths:
            router.add_api_route("/api/talk/v1/health", health, methods=["GET"])
            router.add_api_route("/api/talk/v1/voices", get_voices, methods=["GET"])
            router.add_api_route(
                "/api/talk/v1/speech-syntheses",
                synthesize,
                methods=["POST"],
                response_class=Response,
                responses=SPEECH_SYNTHESIS_RESPONSES,
            )
            router.add_api_route("/api/talk/v1/cache", clear_cache, methods=["DELETE"])
            router.add_api_route("/api/talk/v1/cache/{voice_name}", clear_cache, methods=["DELETE"])

        return router
