"""HTTP client for the platform APIs (tests swap in a mock transport)."""
import httpx2

TRANSPORT = None   # tests: httpx2.MockTransport(handler)


class PlatformError(Exception):
    """A platform rejected a call. `code` is stable for the web; `detail` is for logs."""

    def __init__(self, code: str, detail: str = "", retry: bool = False):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.retry = code, detail, retry


def client(timeout: float = 60.0) -> httpx2.Client:
    return httpx2.Client(transport=TRANSPORT, timeout=timeout, follow_redirects=True)


def check(resp, what: str) -> dict:
    """JSON body of a successful response; PlatformError otherwise."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code >= 400 or (isinstance(body, dict) and body.get("error") and not body.get("data")):
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else str(err or "")) or resp.text[:300]
        code = (err.get("code") if isinstance(err, dict) else None)
        auth = resp.status_code in (401, 403) or code in (190, "access_token_invalid", "access_token_expired")
        raise PlatformError("account_expired" if auth else f"{what}_failed", str(msg)[:500],
                            retry=resp.status_code in (429, 500, 502, 503, 504))
    return body
