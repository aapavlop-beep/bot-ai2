from __future__ import annotations

import asyncio
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, async_playwright


@dataclass(slots=True)
class BrowserResearch:
    """Single browser session used by all sports adapters.

    The project deliberately keeps data acquisition in this layer: AI is not
    allowed to invent schedules, scores or bookmaker lines.
    """

    browser: Browser | None = None
    context: BrowserContext | None = None

    async def start(self) -> None:
        pw = await async_playwright().start()
        self.browser = await pw.chromium.launch(headless=True)
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


async def browser_search(query: str, engine: str = "yandex") -> list[dict[str, str]]:
    """Search through a normal browser page; no search API is used."""
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
            from urllib.parse import quote_plus
            await page.goto(urls[engine] + quote_plus(query), wait_until="domcontentloaded", timeout=20_000)
            links = await page.locator("a").evaluate_all(
                "els => els.map(a => ({title:(a.innerText||a.textContent||'').trim(), href:a.href}))"
            )
            return [x for x in links if x.get("title") and x.get("href")][:30]
        finally:
            await context.close()
            await browser.close()
