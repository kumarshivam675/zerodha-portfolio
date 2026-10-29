"""Cross-site request protection for an app with no login.

Two layers: TrustedHostMiddleware (main.py) refuses unexpected Host headers, which stops DNS
rebinding from reading data; this middleware refuses cross-site state-changing requests.

The API binds to 127.0.0.1 and has no authentication, so any web page the user visits could otherwise
make their browser POST to it (spend API credit, post to Slack, sync Kite). Browsers label such requests
with `Origin` and `Sec-Fetch-Site`; state-changing requests carrying a foreign origin are refused.
Requests without those headers (curl, the Next.js server, the scheduler) are not browser cross-site
requests and pass through.
"""

import json
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class SameOriginWrites:
    def __init__(self, app: ASGIApp, allowed_origins: set[str]):
        self.app = app
        self.allowed = {origin_of(o) for o in allowed_origins}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS and self._cross_site(scope):
            body = json.dumps({"detail": "Cross-site request refused"}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json")]})  # fmt: skip
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _cross_site(self, scope: Scope) -> bool:
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if headers.get("sec-fetch-site") == "cross-site":
            return True
        origin = headers.get("origin")
        return origin is not None and origin_of(origin) not in self.allowed
