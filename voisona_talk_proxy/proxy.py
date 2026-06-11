import logging
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from .client import VoisonaTalkClient


logger = logging.getLogger(__name__)


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

        async def get_voices():
            try:
                return await self.client.get_voices()
            except httpx.HTTPStatusError as exc:
                raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
            except httpx.RequestError as exc:
                raise HTTPException(status_code=502, detail=str(exc))

        async def synthesize(request: Request):
            try:
                payload = await request.json()
                audio = await self.client.synthesize(payload)
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
        router.add_api_route("/speech-syntheses", synthesize, methods=["POST"])
        router.add_api_route("/cache", clear_cache, methods=["DELETE"])
        router.add_api_route("/cache/{voice_name}", clear_cache, methods=["DELETE"])

        if include_compat_paths:
            router.add_api_route("/api/talk/v1/health", health, methods=["GET"])
            router.add_api_route("/api/talk/v1/voices", get_voices, methods=["GET"])
            router.add_api_route("/api/talk/v1/speech-syntheses", synthesize, methods=["POST"])
            router.add_api_route("/api/talk/v1/cache", clear_cache, methods=["DELETE"])
            router.add_api_route("/api/talk/v1/cache/{voice_name}", clear_cache, methods=["DELETE"])

        return router
