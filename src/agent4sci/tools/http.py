"""Shared async HTTP client with per-host rate limits, retries and an on-disk cache.

Scholarly APIs have strict limits (arXiv: one request every 3 s on a single
connection; Crossref: 3 concurrent in the polite pool), so every tool goes
through this client instead of making its own requests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2

from .. import __version__


@dataclass
class HostPolicy:
    min_interval: float = 0.0  # seconds between request starts
    concurrency: int = 8


DEFAULT_POLICIES: dict[str, HostPolicy] = {
    "export.arxiv.org": HostPolicy(3.1, 1),
    "api.semanticscholar.org": HostPolicy(1.05, 1),
    "api.crossref.org": HostPolicy(0.35, 3),
    "eutils.ncbi.nlm.nih.gov": HostPolicy(0.34, 3),
    "api.openalex.org": HostPolicy(0.05, 5),
    "doi.org": HostPolicy(0.2, 4),
}


class _Limiter:
    def __init__(self, policy: HostPolicy) -> None:
        self.policy = policy
        self.sem = asyncio.Semaphore(policy.concurrency)
        self.lock = asyncio.Lock()
        self.last = 0.0

    async def __aenter__(self) -> None:
        await self.sem.acquire()
        async with self.lock:
            wait = self.policy.min_interval - (time.monotonic() - self.last)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last = time.monotonic()

    async def __aexit__(self, *exc: Any) -> None:
        self.sem.release()


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str
    from_cache: bool = False

    @property
    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        return self.content.decode(charset, errors="replace")

    def json(self) -> Any:
        return json.loads(self.content)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class HttpClient:
    def __init__(
        self,
        cache_dir: Path | None = None,
        transport: Any = None,
        policies: dict[str, HostPolicy] | None = None,
        max_retries: int = 4,
        contact_email: str | None = None,
    ) -> None:
        agent = f"agent4sci/{__version__} (research agent"
        agent += f"; mailto:{contact_email})" if contact_email else ")"
        kwargs: dict[str, Any] = {
            "timeout": httpx2.Timeout(30.0, read=90.0),
            "follow_redirects": True,
            "headers": {"User-Agent": agent},
        }
        if transport is not None:
            kwargs["transport"] = transport
        self.client = httpx2.AsyncClient(**kwargs)
        self.cache_dir = cache_dir
        self.policies = {**DEFAULT_POLICIES, **(policies or {})}
        self.max_retries = max_retries
        self._limiters: dict[str, _Limiter] = {}
        self.contact_email = contact_email

    def _limiter(self, host: str) -> _Limiter:
        if host not in self._limiters:
            self._limiters[host] = _Limiter(self.policies.get(host, HostPolicy()))
        return self._limiters[host]

    def _cache_path(self, key: str) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / digest[:2] / f"{digest}.json"

    def _cache_get(self, key: str, ttl: float) -> HttpResponse | None:
        path = self._cache_path(key)
        if path is None or not path.exists() or time.time() - path.stat().st_mtime > ttl:
            return None
        try:
            data = json.loads(path.read_text("utf-8"))
            return HttpResponse(data["status"], data["headers"], bytes.fromhex(data["body"]), data["url"], True)
        except (OSError, ValueError, KeyError):
            return None

    def _cache_put(self, key: str, resp: HttpResponse) -> None:
        path = self._cache_path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        headers = {k: v for k, v in resp.headers.items() if k in ("content-type",)}
        path.write_text(json.dumps({"status": resp.status_code, "headers": headers, "body": resp.content.hex(),
                                    "url": resp.url}), "utf-8")

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        data: Any = None,
        cache_ttl: float | None = None,
        max_bytes: int = 30_000_000,
        raise_for_status: bool = True,
    ) -> HttpResponse:
        key = json.dumps([method, url, sorted((params or {}).items()), json_body, data,
                          sorted((headers or {}).items())], default=str)
        if cache_ttl:
            cached = self._cache_get(key, cache_ttl)
            if cached is not None:
                return cached
        host = urlsplit(url).hostname or ""
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._limiter(host):
                    resp = await self.client.request(method, url, params=params, headers=headers,
                                                     json=json_body, data=data)
            except (httpx2.TimeoutException, httpx2.TransportError) as exc:
                if attempt > self.max_retries:
                    raise HttpError(f"网络错误: {type(exc).__name__}: {exc}") from exc
                await asyncio.sleep(min(30.0, 2.0 ** attempt))
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt <= self.max_retries:
                retry_after = resp.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else 2.0 ** (attempt + 1)
                except ValueError:
                    delay = 2.0 ** (attempt + 1)
                await asyncio.sleep(min(delay, 60.0))
                continue
            content = resp.content[:max_bytes]
            result = HttpResponse(resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, content,
                                  str(resp.url))
            if raise_for_status and not result.ok:
                raise HttpError(f"HTTP {resp.status_code} from {host}: {result.text[:300]}", resp.status_code)
            if cache_ttl and result.ok:
                self._cache_put(key, result)
            return result

    async def get(self, url: str, **kw: Any) -> HttpResponse:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> HttpResponse:
        return await self.request("POST", url, **kw)

    async def aclose(self) -> None:
        await self.client.aclose()
