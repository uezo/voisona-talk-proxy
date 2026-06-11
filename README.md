# 🎙️ VoiSona Talk Proxy

A tiny proxy that makes local VoiSona Talk speech synthesis available over the network.

> NOTE: This is an unofficial project and is not affiliated with, endorsed by, or supported by the maker of VoiSona Talk. Please do not contact them for support about this package.

## 💎 Features

- 🌐 Access [VoiSona Talk](https://voisona.com/talk/) speech synthesis from other machines over the network
- ⚡️ Respond faster by reusing cached audio for repeated requests
- 🐍 Provide Python client for direct local use

## 📦 Installation

Requirements:

- Python 3.11+
- VoiSona Talk running locally
- Local VoiSona Talk API available at `http://127.0.0.1:32766/api/talk/v1`

Install:

```bash
pip install voisona-talk-proxy
```


## 🚀 Quick Start

Set your VoiSona Talk credentials:

```bash
export VOISONA_USERNAME=your-voisona-talk-user
export VOISONA_PASSWORD=your-voisona-talk-password
```

Start the proxy server:

```bash
voisona-talk-proxy --host 0.0.0.0 --port 32777
```

Underscore command and Python module forms are also available:

```bash
voisona_talk_proxy --host 0.0.0.0 --port 32777
python -m voisona_talk_proxy --host 0.0.0.0 --port 32777
```

Synthesize speech:

```bash
curl -X POST http://127.0.0.1:32777/speech-syntheses \
  -H "Content-Type: application/json" \
  -o output.wav \
  -d '{
    "text": "こんにちは",
    "language": "ja_JP",
    "voice_name": "tanaka-san_ja_JP"
  }'
```

You can also mount the proxy inside your own FastAPI app:

```python
from fastapi import FastAPI
from voisona_talk_proxy.proxy import VoisonaProxy

app = FastAPI()
proxy = VoisonaProxy(
    username="your-voisona-talk-user",
    password="your-voisona-talk-password",
)
app.include_router(proxy.get_api_router())
```


## 🧩 API Usage

Routes:

- `GET /voices`
- `POST /speech-syntheses`
- `DELETE /cache`
- `DELETE /cache/{voice_name}`
- `GET /health`
- compatible `/api/talk/v1/...` paths for all routes above

> NOTE: See the [official VoiSona Talk API manual](https://manual.voisona.com/ja/talk/pc/2b6e9bc7efb180ea86ccc6c7347e9ca6) for request payload details.

### GET /voices

Returns installed voice libraries from the local VoiSona Talk API.

```bash
curl http://127.0.0.1:32777/voices
```

Example shape:

```json
{
  "items": [
    {
      "voice_name": "tanaka-san_ja_JP",
      "voice_version": "2.0.1",
      "languages": ["ja_JP"]
    }
  ]
}
```

### POST /speech-syntheses

Synthesizes speech and returns `audio/wav`.

Minimal request:

```bash
curl -X POST http://127.0.0.1:32777/speech-syntheses \
  -H "Content-Type: application/json" \
  -o output.wav \
  -d '{
    "text": "Hello!",
    "language": "ja_JP",
    "voice_name": "tanaka-san_ja_JP"
  }'
```

Full request example with `/voices` values and `global_parameters`:

```python
import httpx

base_url = "http://127.0.0.1:32777"

voices = httpx.get(f"{base_url}/voices").json()
voice = voices["items"][0]

payload = {
    "text": "こんにちは",
    "language": voice["languages"][0],
    "voice_name": voice["voice_name"],
    "voice_version": voice["voice_version"],
    "global_parameters": {
        "alp": 0.0,
        "huskiness": 0.0,
        "intonation": 1.0,
        "pitch": 0.0,
        "speed": 2.0,
        "style_weights": [],
        "volume": 0.0,
    },
}

audio = httpx.post(f"{base_url}/speech-syntheses", json=payload).content

with open("output.wav", "wb") as f:
    f.write(audio)
```

### GET /health

Returns a simple health check response.

```bash
curl http://127.0.0.1:32777/health
```

```json
{"status": "ok"}
```

### DELETE /cache

Clears cached audio for the default voice cache.

```bash
curl -X DELETE http://127.0.0.1:32777/cache
```

```json
{"cleared": true, "voice_name": "default"}
```

You can also clear one voice cache by path or query parameter:

```bash
curl -X DELETE http://127.0.0.1:32777/cache/tanaka-san_ja_JP
curl -X DELETE "http://127.0.0.1:32777/cache?voice_name=tanaka-san_ja_JP"
```


## 🍪 Cache

The proxy caches generated audio under `voisona_talk_cache/<voice_name>/<cache_key>.wav`.

If `voice_name` is missing, the cache goes under `default`.

Set the proxy cache directory with `--cache-dir`:

```bash
voisona-talk-proxy --host 0.0.0.0 --port 32777 --cache-dir /path/to/cache
```

Or use `VOISONA_CACHE_DIR`:

```bash
export VOISONA_CACHE_DIR=/path/to/cache
voisona-talk-proxy --host 0.0.0.0 --port 32777
```

When mounting the proxy in your own FastAPI app, pass `cache_dir` to `VoisonaProxy`:

```python
from fastapi import FastAPI
from voisona_talk_proxy.proxy import VoisonaProxy

app = FastAPI()
proxy = VoisonaProxy(
    username="your-voisona-talk-user",
    password="your-voisona-talk-password",
    cache_dir="/path/to/cache",
)
app.include_router(proxy.get_api_router())
```


## ⚙️ Configurations

CLI arguments take precedence over environment variables.

| Setting | CLI argument | Environment variable | Default |
| --- | --- | --- | --- |
| Listen host | `--host` | - | `127.0.0.1` |
| Listen port | `--port` | - | `32777` |
| VoiSona Talk API URL | `--voisona-url` | `VOISONA_BASE_URL` | `http://127.0.0.1:32766/api/talk/v1` |
| Username | `--username` | `VOISONA_USERNAME` | - |
| Password | `--password` | `VOISONA_PASSWORD` | - |
| Cache directory | `--cache-dir` | `VOISONA_CACHE_DIR` | `voisona_talk_cache` |
| Log level | `--log-level` | `VOISONA_LOG_LEVEL` | `info` |

Example:

```bash
export VOISONA_BASE_URL=http://127.0.0.1:32766/api/talk/v1
export VOISONA_USERNAME=your-voisona-talk-user
export VOISONA_PASSWORD=your-voisona-talk-password
export VOISONA_CACHE_DIR=/path/to/cache
export VOISONA_LOG_LEVEL=debug

voisona-talk-proxy --host 0.0.0.0 --port 32777
```

> NOTE: For more advanced FastAPI or Uvicorn settings, create your own main program and mount `VoisonaProxy` there.


## 🐍 Python Local Mode

```python
from voisona_talk_proxy.client import VoisonaTalkClient

client = VoisonaTalkClient(
    base_url="http://127.0.0.1:32766/api/talk/v1",
)

audio = await client.synthesize({
    "text": "Hello!",
    "language": "ja_JP",
    "voice_name": "tanaka-san_ja_JP",
})

with open("output.wav", "wb") as f:
    f.write(audio)

await client.close()
```

You can also use `get_voices()` and pass values from an installed voice library. This is useful when you want to choose voices dynamically.

```python
voices = await client.get_voices()
voice = voices["items"][0]

audio = await client.synthesize({
    "text": "Hello!",
    "language": voice["languages"][0],
    "voice_name": voice["voice_name"],
    "voice_version": voice["voice_version"],
})
```


## 🧪 Tests

The tests use the real local VoiSona Talk API, not mocks. Put your local API URL and credentials in `pytest.ini`, then run:

```bash
python -m pytest tests/
```


## 🌧️ Thanks

We built this repository to make [🌧️ 雨衣 / Ui](https://voisona.com/talk/artist/ui_ja_JP/)'s voice easier to use in all kinds of scenes. Thank you for the wonderful voice! ✨🙏✨

- X: [@ui_roid](https://x.com/ui_roid)
- Official: [雨衣(うい) Official](https://www.ui-roid.com)


## ⚖️ License

MIT. Use it freely, and please share what you create with it on SNS!
