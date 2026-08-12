from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    url: str
    status: int
    body: str
    final_url: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 400


def fetch_result(url: str, timeout: int = 30) -> FetchResult:
    """Fetch URL without bypassing anti-bot; returns status + body or error."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
            status = getattr(resp, "status", None) or resp.getcode() or 200
            final = resp.geturl() if hasattr(resp, "geturl") else url
            return FetchResult(url=url, status=int(status), body=body, final_url=final)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return FetchResult(
            url=url,
            status=int(exc.code),
            body=body,
            error=f"HTTP {exc.code} for {url}",
        )
    except urllib.error.URLError as exc:
        return FetchResult(
            url=url,
            status=0,
            body="",
            error=f"URL error for {url}: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return FetchResult(url=url, status=0, body="", error=str(exc))


def fetch_text(url: str, timeout: int = 30) -> str:
    """Back-compat: raise on failure, return body on success."""
    res = fetch_result(url, timeout=timeout)
    if not res.ok:
        raise RuntimeError(res.error or f"fetch failed for {url}")
    return res.body
