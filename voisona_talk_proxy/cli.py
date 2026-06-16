import argparse
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from .proxy import VoisonaProxy


logger = logging.getLogger(__name__)


def create_app(
    *,
    voisona_url: str = None,
    username: str = None,
    password: str = None,
    cache_dir: str = None,
) -> FastAPI:
    proxy = VoisonaProxy(
        voisona_url=voisona_url
        or os.getenv("VOISONA_BASE_URL", "http://127.0.0.1:32766/api/talk/v1"),
        username=username or os.getenv("VOISONA_USERNAME"),
        password=password or os.getenv("VOISONA_PASSWORD"),
        cache_dir=cache_dir or os.getenv("VOISONA_CACHE_DIR"),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await proxy.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(proxy.get_api_router())
    static_dir = Path(__file__).parent / "static"
    app.mount(
        "/playground",
        StaticFiles(directory=static_dir / "playground", html=True),
        name="playground",
    )
    return app


def parse_args():
    parser = argparse.ArgumentParser(prog="voisona-talk-proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=32777)
    parser.add_argument("--voisona-url", default=None)
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument("--log-level", default=None)
    return parser.parse_args()


def configure_logging(log_level: str):
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def main():
    args = parse_args()
    log_level = args.log_level or os.getenv("VOISONA_LOG_LEVEL", "info")
    configure_logging(log_level)
    app = create_app(
        voisona_url=args.voisona_url,
        username=args.username,
        password=args.password,
        cache_dir=args.cache_dir,
    )
    logger.info("Starting VoiSona Talk proxy on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level=log_level)


if __name__ == "__main__":
    main()
