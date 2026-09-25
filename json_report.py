import json
from typing import Any


def write_json_report(
    page_data: dict[str, Any], filename: str = "report.json"
) -> None:
    pages = sorted(page_data.values(), key=lambda p: p["url"])
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(pages, f, indent=2)
