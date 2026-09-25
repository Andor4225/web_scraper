import asyncio
import sys
from crawl import crawl_site_async
from json_report import write_json_report


async def main() -> None:
    args = sys.argv[1:]

    if len(args) < 1:
        print("no website provided")
        sys.exit(1)

    if len(args) > 3:
        print("too many arguments provided")
        sys.exit(1)

    base_url = args[0]
    max_concurrency = int(args[1]) if len(args) > 1 else 3
    max_pages = int(args[2]) if len(args) > 2 else 10

    print(
        f"starting crawl of: {base_url} (max concurrency: {max_concurrency}, max pages: {max_pages})"
    )

    page_data = await crawl_site_async(
        base_url,
        max_concurrency=max_concurrency,
        max_pages=max_pages,
    )

    write_json_report(page_data)


if __name__ == "__main__":
    asyncio.run(main())
