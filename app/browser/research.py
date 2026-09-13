from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import quote_plus, urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


SEARCH_TIMEOUT_MS = 12_000
PAGE_TIMEOUT_MS = 20_000


@dataclass(slots=True)
class BrowserResearch:
    """Reusable browser-only acquisition layer."""

    browser: Browser | None = None
    context: BrowserContext | None = None
    _playwright = None
    _lock: asyncio.Lock | None = None

    async def start(self) -> None:
        if self.browser and self.context:
            return
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        )
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        if self.context:
            await self.context.close()
            self.context = None
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def open(self, url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> Page:
        await self.start()
        assert self.context is not None
        page = await self.context.new_page()
        # Sports pages may keep analytics/advertising requests open for a long
        # time. We only need the rendered document, so commit is more reliable
        # than waiting for DOMContentLoaded.
        await page.goto(url, wait_until="commit", timeout=timeout_ms)
        return page

    async def text(self, url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> str:
        page = await self.open(url, timeout_ms)
        try:
            await page.wait_for_timeout(1_000)
            return await page.locator("body").inner_text(timeout=timeout_ms)
        finally:
            await page.close()


_shared: BrowserResearch | None = None
_shared_guard = asyncio.Lock()


async def _get_shared() -> BrowserResearch:
    global _shared
    async with _shared_guard:
        if _shared is None:
            _shared = BrowserResearch()
            await _shared.start()
        return _shared


async def browser_source_text(url: str, timeout_ms: int = PAGE_TIMEOUT_MS) -> str:
    """Read visible text from a real page using one reusable Playwright browser."""
    research = await _get_shared()
    lock = research._lock
    if lock is None:
        raise RuntimeError("Browser research lock is not initialized")
    async with lock:
        page = await research.open(url, timeout_ms)
        try:
            await page.wait_for_timeout(1_000)
            body = page.locator("body")
            await body.wait_for(state="visible", timeout=timeout_ms)
            return await body.inner_text(timeout=timeout_ms)
        finally:
            await page.close()


async def browser_search(query: str, engine: str = "yandex") -> list[dict[str, str]]:
    """Browser search for discovery only; search results themselves are never evidence."""
    urls = {
        "yandex": "https://yandex.ru/search/?text=",
        "google": "https://www.google.com/search?q=",
    }
    if engine not in urls:
        raise ValueError(f"Unsupported browser search engine: {engine}")

    research = await _get_shared()
    lock = research._lock
    if lock is None or research.context is None:
        raise RuntimeError("Browser research is not initialized")
    async with lock:
        page = await research.context.new_page()
        try:
            await page.goto(urls[engine] + quote_plus(query), wait_until="commit", timeout=SEARCH_TIMEOUT_MS)
            await page.wait_for_timeout(800)
            links = await page.locator("a").evaluate_all(
                "els => els.map(a => ({title:(a.innerText||a.textContent||'').trim(), href:a.href}))"
            )
            return [x for x in links if x.get("title") and x.get("href")][:30]
        finally:
            await page.close()


async def _browser_search_pages_engine(query: str, max_pages: int, engine: str) -> list[tuple[str, str]]:
    urls = {
        "yandex": "https://yandex.ru/search/?text=",
        "google": "https://www.google.com/search?q=",
    }
    if engine not in urls:
        raise ValueError(f"Unsupported browser search engine: {engine}")

    research = await _get_shared()
    lock = research._lock
    if lock is None or research.context is None:
        raise RuntimeError("Browser research is not initialized")

    async with lock:
        search_page = await research.context.new_page()
        try:
            await search_page.goto(urls[engine] + quote_plus(query), wait_until="commit", timeout=SEARCH_TIMEOUT_MS)
            await search_page.wait_for_timeout(800)
            links = await search_page.locator("a").evaluate_all(
                "els => els.map(a => ({title:(a.innerText||a.textContent||'').trim(), href:a.href}))"
            )
        finally:
            await search_page.close()

        blocked_hosts = {"yandex.ru", "www.yandex.ru", "google.com", "www.google.com", "youtube.com", "www.youtube.com"}
        candidates: list[str] = []
        seen: set[str] = set()
        for item in links:
            url = (item.get("href") or "").strip()
            if not url.startswith("http"):
                continue
            parsed = urlparse(url)
            if parsed.netloc.lower() in blocked_hosts or url in seen:
                continue
            seen.add(url)
            candidates.append(url)
            if len(candidates) >= max_pages * 3:
                break

        result: list[tuple[str, str]] = []
        page = await research.context.new_page()
        try:
            for url in candidates:
                if len(result) >= max_pages:
                    break
                try:
                    await page.goto(url, wait_until="commit", timeout=PAGE_TIMEOUT_MS)
                    await page.wait_for_timeout(700)
                    text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
                    if text and len(text.strip()) >= 200:
                        result.append((url, text))
                except Exception:
                    continue
        finally:
            await page.close()

    return result


async def browser_search_pages(query: str, max_pages: int = 3, engine: str = "yandex") -> list[tuple[str, str]]:
    """Discover and open real pages with bounded time and a reusable browser."""
    timeout = max(45, 20 + 12 * max_pages)
    try:
        return await asyncio.wait_for(
            _browser_search_pages_engine(query, max_pages, engine),
            timeout=timeout,
        )
    except Exception:
        return []
