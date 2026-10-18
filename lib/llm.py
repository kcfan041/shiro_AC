from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque

import aiohttp

log = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 timeout: float = 90, max_per_minute: int = 60, retries: int = 1):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.timeout = timeout
        self.max_per_minute = max_per_minute
        self.retries = retries
        self._calls: deque[float] = deque()
        self._session: aiohttp.ClientSession | None = None

    RESERVE = {1: 0.0, 2: 0.0, 3: 0.10, 4: 0.25}

    def remaining(self) -> int:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        return self.max_per_minute - len(self._calls)

    def can_call(self, priority: int = 1) -> bool:
        return self.remaining() > self.max_per_minute * self.RESERVE.get(priority, 0.0)

    def _allow(self, priority: int = 1) -> bool:
        if not self.can_call(priority):
            return False
        self._calls.append(time.monotonic())
        return True

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def chat(self, messages: list[dict], json_mode: bool = False, temperature: float = 0.8,
                   priority: int = 1) -> str | None:
        if not self.base_url or not self.model:
            log.warning("LLM 未設定 LLM_BASE_URL / LLM_MODEL")
            return None
        if not self._allow(priority):
            log.warning("LLM rate limit（priority %s）", priority)
            return None
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        session = await self._get_session()
        for attempt in range(self.retries + 1):
            try:
                async with session.post(f"{self.base_url}/chat/completions", json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning("LLM HTTP %s: %s", resp.status, body[:300])
                    else:
                        data = await resp.json()
                        return data["choices"][0]["message"]["content"]
            except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, IndexError) as e:
                log.warning("LLM request failed (%s): %r", attempt + 1, e)
            if attempt < self.retries:
                await asyncio.sleep(1.5)
        return None

    async def chat_json(self, system: str, user: str, temperature: float = 0.8, priority: int = 1) -> dict | None:
        raw = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            json_mode=True, temperature=temperature, priority=priority,
        )
        return parse_json(raw)


def parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    text = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None
