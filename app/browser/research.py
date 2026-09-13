from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


@dataclass(slots=True)
class BrowserResearch:
    """Browser-only acquisition layer.

    Search is used only for discovery. Sports events must be read from the
    actual source page before they can become confirmed events.
    """

    browser: Browser | None = None
    context: BrowserContext | None = None
    _playwright = None

    async def start(self) -> None:
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

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def open(self, url: str, timeout_ms: int = 20_000) -> Page:
        if not self.context:
            raise RuntimeError("BrowserResearch is not started")
        page = await self.context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return page

    async def text(self, url: str, timeout_ms: int = 20_000) -> str:
        page = await self.open(url, timeout_ms)
        try:
            return await page.locator("body").inner_text(timeout=timeout_ms)
        finally:
            await page.close()


async def browser_source_text(url: str, timeout_ms: int = 30_000) -> str:
    """Read the visible text of a real source page through Playwright."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            return await page.locator("body").inner_text(timeout=timeout_ms)
        finally:
            await context.close()
            await browser.close()


async def browser_search(query: str, engine: str = "yandex") -> list[dict[str, str]]:
    """Browser search for discovery only; never treats search links as matches."""
    urls = {
        "yandex": "https://yandex.ru/search/?text=",
        "google": "https://www.google.com/search?q=",
    }
    if engine not in urls:
        raise ValueError(f"Unsupported browser search engine: {engine}")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(locale="ru-RU")
        page = await context.new_page()
        try:
            await page.goto(
                urls[engine] + quote_plus(query),
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            links = await page.locator("a").evaluate_all(
                "els => els.map(a => ({title:(a.innerText||a.textContent||'').trim(), href:a.href}))"
            )
            return [x for x in links if x.get("title") and x.get("href")][:30]
        finally:
            await context.close()
            await browser.close()
