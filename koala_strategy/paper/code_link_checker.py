from __future__ import annotations

from urllib.parse import urlparse

import requests


def extract_code_urls(text: str) -> list[str]:
    import re

    urls = re.findall(r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org)/[^\s)\]}>,]+", text or "")
    return sorted(set(urls))


def check_url(url: str, timeout: int = 10) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 405:
            response = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
        return 200 <= response.status_code < 400
    except requests.RequestException:
        return False

