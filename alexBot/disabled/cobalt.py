import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Literal, Optional

import urllib.request
import logging

import aiohttp

ENDPOINT = os.environ.get("COBALT_URL") or "http://cobalt-api:9000"
FALLBACK_ENDPOINT = "https://api.cobalt.tools/"

DEFAULT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "alexBot/1.0"}

log = logging.getLogger(__name__)


@dataclass
class RequestBody:
    url: str
    videoQuality: Literal["max", "4320", "2160", "1440", "1080", "720", "480", "360", "240", "144"] = "720"
    audioFormat: Literal["best", "mp3", "ogg", "wav", "opus"] = "mp3"
    filenameStyle: Literal["classic", "pretty", "basic", "nerdy"] = "classic"
    downloadMode: Literal["auto", "audio", "mute"] = "auto"
    youtubeVideoCodec: Literal["h264", "av1", "vp9"] = "h264"
    youtubeDubLang: Optional[Literal["en", "ru", "cs", "ja"]] = None
    youtubeDubBrowserLang: bool = False
    alwaysProxy: bool = False
    disableMetadata: bool = False
    tiktokFullAudio: bool = False
    tiktokH265: bool = False
    twitterGif: bool = True

    def dict(self):
        without_none = {k: v for k, v in asdict(self).items() if v is not None}
        return without_none


@dataclass
class Picker:
    url: str
    type: Optional[Literal["video", "photo", "gif"]] = None
    thumb: Optional[str] = None
    data: Optional[bytes] = None

    async def fetch(self, session: aiohttp.ClientSession) -> bytes:
        async with session.get(self.url) as response:
            self.data = await response.read()
            return self.data


@dataclass
class ResponceBody:
    status: Literal["error", "redirect", "tunnel", "picker"]
    error: Optional[str] = None

    # status: "redirect", "tunnel"
    url: Optional[str] = None
    filename: Optional[str] = None

    # status: "picker"
    audio: Optional[str] = None
    audioFilename: Optional[str] = None
    picker: Optional[List[Picker]] = None
    _picker: Optional[List[Dict]] = None


@dataclass
class CobaltServerData:
    version: str
    url: str
    startTime: str
    durationLimit: int
    services: List[str]


@dataclass
class GitServerData:
    commit: str
    branch: str
    remote: str


@dataclass
class ServerInfo:
    cobalt: CobaltServerData
    git: GitServerData


class Cobalt:
    def __init__(self) -> None:
        # make a request to ENDPOINT and check if it's up, if not, set to fallback server
        try:
            contents = urllib.request.urlopen(ENDPOINT).read()
        except Exception:
            contents = None
        if contents is None:
            self.endpoint = FALLBACK_ENDPOINT
            log.warning(f"Cobalt API {ENDPOINT} is down, using fallback server ({FALLBACK_ENDPOINT})")
        else:
            self.endpoint = ENDPOINT
        self.headers = DEFAULT_HEADERS

    async def get_server_info(self) -> ServerInfo:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.get(self.endpoint) as resp:
                data = await resp.json()
                return ServerInfo(cobalt=CobaltServerData(**data['cobalt']), git=GitServerData(**data['git']))

    async def process(self, request_body: RequestBody) -> ResponceBody:
        async with aiohttp.ClientSession(headers=self.headers) as session:
            async with session.post(self.endpoint, json=request_body.dict()) as resp:
                rb = ResponceBody(**await resp.json())
                if rb.status == "picker":
                    rb._picker = rb.picker
                    rb.picker = [Picker(**p) for p in rb._picker]  # type: ignore

                return rb
