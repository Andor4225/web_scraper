import asyncio
from typing import Any, TypedDict
from urllib.parse import urljoin, urlsplit
import aiohttp
from bs4 import BeautifulSoup, Tag


class PageData(TypedDict):
    url: str
    heading: str
    first_paragraph: str
    outgoing_links: list[str]
    image_urls: list[str]


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    combined = f"{parsed.netloc}{parsed.path}"
    return combined.rstrip("/")


def get_heading_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    heading_tag = soup.find("h1") or soup.find("h2")
    return heading_tag.get_text(strip=True) if isinstance(heading_tag, Tag) else ""


def get_first_paragraph_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    main_tag = soup.find("main")

    if isinstance(main_tag, Tag):
        main_p = main_tag.find("p")
        if isinstance(main_p, Tag):
            return main_p.get_text(strip=True)

    first_p = soup.find("p")
    return first_p.get_text(strip=True) if isinstance(first_p, Tag) else ""


def get_urls_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href:
            urls.append(urljoin(base_url, href))

    return urls


def get_images_from_html(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    image_urls: list[str] = []

    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            image_urls.append(urljoin(base_url, src))

    return image_urls


def extract_page_data(html: str, page_url: str) -> PageData:
    return {
        "url": page_url,
        "heading": get_heading_from_html(html),
        "first_paragraph": get_first_paragraph_from_html(html),
        "outgoing_links": get_urls_from_html(html, page_url),
        "image_urls": get_images_from_html(html, page_url),
    }


class AsyncCrawler:
    def __init__(
        self,
        base_url: str,
        max_concurrency: int = 3,
        max_pages: int = 10,
    ) -> None:
        self.base_url = base_url
        self.base_domain = urlsplit(base_url).netloc
        self.page_data: dict[str, PageData] = {}
        self.visited: set[str] = set()
        self.lock = asyncio.Lock()
        self.max_concurrency = max_concurrency
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.session: aiohttp.ClientSession | None = None
        self.max_pages = max_pages
        self.should_stop = False
        self.all_tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> "AsyncCrawler":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self.session:
            await self.session.close()

    async def add_page_visit(self, normalized_url: str) -> bool:
        async with self.lock:
            if self.should_stop:
                return False

            if len(self.visited) >= self.max_pages:
                self.should_stop = True
                print("Reached maximum number of pages to crawl.")
                return False

            if normalized_url in self.visited:
                return False

            self.visited.add(normalized_url)
            return True

    async def get_html(self, url: str) -> str:
        if not self.session:
            raise RuntimeError("ClientSession is not initialized. Use inside an async context manager.")

        headers = {"User-Agent": "BootCrawler/1.0"}
        async with self.session.get(url, headers=headers) as response:
            if response.status >= 400:
                raise Exception(f"HTTP error: status code {response.status}")

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                raise Exception(f"Invalid content type: {content_type}, expected text/html")

            return await response.text()

    async def crawl_page(self, current_url: str | None = None) -> None:
        if self.should_stop:
            return

        if current_url is None:
            current_url = self.base_url

        current_domain = urlsplit(current_url).netloc
        if current_domain != self.base_domain:
            return

        normalized_url = normalize_url(current_url)

        is_first_visit = await self.add_page_visit(normalized_url)
        if not is_first_visit:
            return

        print(f"crawling: {current_url}")
        try:
            async with self.semaphore:
                html = await self.get_html(current_url)
        except Exception as e:
            print(f"failed to crawl {current_url}: {e}")
            return

        data = extract_page_data(html, current_url)

        async with self.lock:
            self.page_data[normalized_url] = data

        if self.should_stop:
            return

        child_tasks: list[asyncio.Task[None]] = []
        for next_url in data["outgoing_links"]:
            if self.should_stop:
                break
            task = asyncio.create_task(self.crawl_page(next_url))
            self.all_tasks.add(task)
            child_tasks.append(task)

        if child_tasks:
            try:
                await asyncio.gather(*child_tasks)
            finally:
                for task in child_tasks:
                    self.all_tasks.discard(task)

    async def crawl(self) -> dict[str, PageData]:
        root_task = asyncio.create_task(self.crawl_page(self.base_url))
        self.all_tasks.add(root_task)
        try:
            await root_task
        finally:
            self.all_tasks.discard(root_task)
        return self.page_data


async def crawl_site_async(
    base_url: str,
    max_concurrency: int = 3,
    max_pages: int = 10,
) -> dict[str, PageData]:
    async with AsyncCrawler(
        base_url,
        max_concurrency=max_concurrency,
        max_pages=max_pages,
    ) as crawler:
        return await crawler.crawl()
