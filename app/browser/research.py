from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import parse_qs, quote_plus, urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

SEARCH_TIMEOUT_MS = 12_000
PAGE_TIMEOUT_MS = 20_000


def _public_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not host or parsed.username:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return "." in host


def _domain_allowed(url: str, domains: set[str] | None) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return _public_url(url) and (domains is None or any(host == d or host.endswith("." + d) for d in domains))


@dataclass(slots=True)
class BrowserResearch:
    """One browser, bounded concurrent tabs, no cached LIVE page bodies."""

    browser: Browser | None = None
    context: BrowserContext | None = None
    _playwright: object = None
    _start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(4))

    async def start(self) -> None:
        async with self._start_lock:
            if self.browser and self.browser.is_connected() and self.context:
                return
            try:
                self._playwright = await async_playwright().start()
                self.browser = await self._playwright.chromium.launch(headless=True)
                self.context = await self.browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
            except BaseException:
                await self.close()
                raise

    async def close(self) -> None:
        context, browser, runtime = self.context, self.browser, self._playwright
        self.context = self.browser = self._playwright = None
        try:
            if context:
                await context.close()
        finally:
            try:
                if browser:
                    await browser.close()
            finally:
                if runtime:
                    await runtime.stop()

    async def open(self, url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> Page:
        if not _public_url(url):
            raise ValueError("Only public HTTP(S) source URLs are supported")
        await self.start()
        assert self.context is not None
        page = await self.context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response and response.status >= 400:
                raise RuntimeError(f"Source returned HTTP {response.status}")
            await page.locator("body").wait_for(state="visible", timeout=timeout_ms)
            return page
        except BaseException:
            await page.close()
            raise

    async def document(self, url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> dict[str, str]:
        async with self._slots:
            page = await self.open(url, timeout_ms)
            try:
                # Bounded rendering wait; advertising need never go idle.
                await page.wait_for_timeout(500)
                text = await page.locator("body").inner_text(timeout=timeout_ms)
                return {"url": page.url, "text": text, "html": await page.content(),
                        "observed_at": datetime.now(timezone.utc).isoformat()}
            finally:
                await page.close()

    async def text(self, url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> str:
        return (await self.document(url, timeout_ms))["text"]


_shared: BrowserResearch | None = None
_shared_guard = asyncio.Lock()


async def _get_shared() -> BrowserResearch:
    global _shared
    async with _shared_guard:
        if _shared is None:
            candidate = BrowserResearch()
            await candidate.start()
            _shared = candidate
        return _shared


async def close_browser_research() -> None:
    global _shared
    async with _shared_guard:
        research, _shared = _shared, None
        if research:
            await research.close()


async def browser_source_document(url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> dict[str, str]:
    research = await _get_shared()
    return await asyncio.wait_for(research.document(url, timeout_ms), timeout=timeout_ms / 1000 + 4)


async def browser_source_text(url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> str:
    return (await browser_source_document(url, timeout_ms))["text"]


async def browser_search(query: str, engine: str = "yandex") -> list[dict[str, str]]:
    """Search only discovers links; snippets are never match/price evidence."""
    roots = {"yandex": "https://yandex.ru/search/?text=", "google": "https://www.google.com/search?q="}
    if engine not in roots:
        raise ValueError(f"Unsupported browser search engine: {engine}")
    research = await _get_shared()
    async with research._slots:
        page = await research.open(roots[engine] + quote_plus(query), SEARCH_TIMEOUT_MS)
        try:
            links = await page.locator("a").evaluate_all(
                "els => els.map(a => ({title:(a.innerText||a.textContent||'').trim(), href:a.href}))"
            )
            result = []
            for item in links:
                url = item.get("href", "")
                parsed = urlparse(url)
                if parsed.hostname in {"www.google.com", "google.com"} and parsed.path == "/url":
                    url = parse_qs(parsed.query).get("q", [url])[0]
                host = (urlparse(url).hostname or "").removeprefix("www.")
                if host in {"yandex.ru", "google.com", "ya.ru"} or host.endswith((".yandex.ru", ".google.com")):
                    continue
                if item.get("title") and _public_url(url):
                    result.append({"title": item["title"], "href": url})
            return result[:30]
        finally:
            await page.close()


async def browser_search_pages(query: str, max_pages: int = 3, engine: str = "yandex",
                               allowed_domains: set[str] | None = None) -> list[tuple[str, str]]:
    """Open bounded candidate pages, validating final domains after redirects."""
    if not 1 <= max_pages <= 5:
        raise ValueError("max_pages must be between 1 and 5")
    links = await browser_search(query, engine)
    candidates = list(dict.fromkeys(item["href"] for item in links
                                    if _domain_allowed(item["href"], allowed_domains)))[:max_pages * 2]

    async def read(url: str) -> tuple[str, str] | None:
        try:
            doc = await browser_source_document(url, timeout_ms=12_000)
            if _domain_allowed(doc["url"], allowed_domains) and len(doc["text"].strip()) >= 100:
                return doc["url"], doc["text"]
        except Exception:
            return None
        return None

    pages = await asyncio.gather(*(read(url) for url in candidates))
    return [item for item in pages if item is not None][:max_pages]
