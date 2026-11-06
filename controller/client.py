"""Thin HTTP client for the simulator API with retries on transient errors."""
from __future__ import annotations

import time
from typing import Any

import httpx


class SimClient:
    def __init__(self, url: str, timeout: float = 120.0):
        self.url = url.rstrip("/")
        self.c = httpx.Client(base_url=self.url, timeout=timeout)

    def close(self) -> None:
        self.c.close()

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        for attempt in range(6):
            try:
                r = self.c.request(method, path, **kw)
                if r.status_code >= 500:
                    time.sleep(0.4 * (attempt + 1))
                    continue
                return r
            except (httpx.TransportError, httpx.TimeoutException):
                time.sleep(0.4 * (attempt + 1))
        return self.c.request(method, path, **kw)

    def get(self, path: str, **kw) -> Any:
        r = self._req("GET", path, **kw)
        r.raise_for_status()
        if "application/x-ndjson" in r.headers.get("content-type", "") or path.startswith(
            "/events/chunks/"
        ):
            return r.text
        return r.json()

    def post(self, path: str, json: dict | None = None) -> tuple[int, Any]:
        r = self._req("POST", path, json=json or {})
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        return r.status_code, body

    def put(self, path: str, json: dict | None = None) -> tuple[int, Any]:
        r = self._req("PUT", path, json=json or {})
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        return r.status_code, body

    def get_json(self, path: str, **kw) -> Any:
        r = self._req("GET", path, **kw)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
