"""The browser approves the login, and the code comes back to a listener on this machine.

The CLI starts the sign-in at the portal with a PKCE challenge and a loopback redirect. The browser
signs in with the identity provider, which redirects to the listener with a one-time code and the
state this process sent. The CLI then trades the code, with its PKCE verifier and nonce, for a CLI
login token, which the portal shows only once.
"""

from __future__ import annotations

import html
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from garden.auth.pkce import Challenge, generate_challenge
from garden.shared import GardenError

AUTHORIZE_PATH = "/api/agent/cli/authorize"
CREATE_PATH = "/api/agent/cli/approvals"
CALLBACK_PATH = "/callback"
DEFAULT_TIMEOUT_SECONDS = 300

# The callback page is self-contained: no fonts, scripts or images are fetched. It follows light or
# dark mode, and its colours match the portal's theme.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} - Garden</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    margin: 0; min-height: 100vh; box-sizing: border-box; padding: 16px;
    display: grid; place-items: center;
    background: #f5f6f8; color: #101828;
    font-family: "Inter Variable", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  }}
  main {{
    box-sizing: border-box; width: 100%; max-width: 440px; padding: 32px 28px;
    background: #ffffff; border: 1px solid #d0d5dd; border-radius: 12px;
    box-shadow: 0 1px 2px rgb(16 24 40 / 0.06);
  }}
  .mark {{
    width: 44px; height: 44px; border-radius: 999px;
    display: grid; place-items: center;
  }}
  .mark svg {{ width: 22px; height: 22px; }}
  .ok .mark {{ background: #edf8f1; color: #17663a; }}
  .fail .mark {{ background: #fdf0f0; color: #a51d1d; }}
  h1 {{ margin: 18px 0 8px; font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }}
  p {{ margin: 0; color: #475467; line-height: 1.5; overflow-wrap: anywhere; }}
  .brand {{ margin-top: 24px; font-size: 13px; color: #667085; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #0d1117; color: #f0f2f5; }}
    main {{ background: #161b22; border-color: #30363d; }}
    p {{ color: #a0aec0; }}
    .brand {{ color: #8b949e; }}
    .ok .mark {{ background: rgb(134 239 172 / 0.12); color: #86efac; }}
    .fail .mark {{ background: rgb(248 113 113 / 0.12); color: #fca5a5; }}
  }}
</style>
</head>
<body>
<main class="{kind}">
  <div class="mark" aria-hidden="true">
    {icon}
  </div>
  <h1>{title}</h1>
  <p>{message}</p>
  <p class="brand">Garden</p>
</main>
</body>
</html>
"""

_CHECK_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'
)
_CROSS_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>'
)


@dataclass(frozen=True)
class CallbackResult:
    code: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    device: str


def success_page(device: str) -> str:
    """The page the browser lands on after the approval reached the CLI's listener."""
    return _PAGE.format(
        title="Sign-in succeeded",
        kind="ok",
        icon=_CHECK_ICON,
        message=(
            f"Garden is signed in on {html.escape(device)}. "
            "You can close this tab and return to the terminal."
        ),
    )


def failure_page(message: str) -> str:
    """A page saying the sign-in did not complete, and what to do next."""
    return _PAGE.format(
        title="Sign-in did not complete",
        kind="fail",
        icon=_CROSS_ICON,
        message=html.escape(message),
    )


class _Callback(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self._reply(
                404, failure_page("Nothing is waiting at this address. Return to the terminal.")
            )
            return
        listener = cast("_Listener", self.server)
        query = parse_qs(parts.query)
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        if not code or not secrets.compare_digest(state, listener.state):
            self._reply(
                400, failure_page("This sign-in link is not valid. Run garden login again.")
            )
            return
        listener.code = code
        listener.done.set()
        self._reply(200, success_page(listener.device))

    def _reply(self, status: int, body_text: str) -> None:
        body = body_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _Listener(HTTPServer):
    def __init__(self, state: str, device: str) -> None:
        super().__init__(("127.0.0.1", 0), _Callback)
        self.state = state
        self.device = device
        self.code: str | None = None
        self.done = threading.Event()


def authorize_url(
    portal_url: str, *, redirect_uri: str, state: str, nonce: str, challenge: str
) -> str:
    query = urlencode(
        {
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
        }
    )
    return f"{portal_url}{AUTHORIZE_PATH}?{query}"


class LoopbackLoginServer:
    def __init__(
        self,
        portal_url: str,
        device_name: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.portal_url = portal_url.rstrip("/")
        self.device = device_name
        self.timeout_seconds = timeout_seconds
        self.state = secrets.token_urlsafe(24)
        self.nonce = secrets.token_urlsafe(24)
        self.challenge: Challenge = generate_challenge()
        self._listener: _Listener | None = None
        self._worker: threading.Thread | None = None
        self.redirect_uri: str = ""
        self._authorize_url: str = ""

    @property
    def authorize_url(self) -> str:
        if not self._authorize_url:
            self._ensure_server()
        return self._authorize_url

    def _ensure_server(self) -> None:
        if self._listener is not None:
            return
        self._listener = _Listener(self.state, self.device)
        port = self._listener.server_address[1]
        self.redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
        self._authorize_url = authorize_url(
            self.portal_url,
            redirect_uri=self.redirect_uri,
            state=self.state,
            nonce=self.nonce,
            challenge=self.challenge.challenge,
        )
        self._worker = threading.Thread(
            target=lambda: cast(_Listener, self._listener).serve_forever(poll_interval=0.05),
            daemon=True,
        )
        self._worker.start()

    def start(self) -> None:
        self._ensure_server()

    def __enter__(self) -> LoopbackLoginServer:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._listener is not None:
            self._listener.shutdown()
            self._listener.server_close()
            self._listener = None

    def wait_for_callback(self) -> CallbackResult:
        self._ensure_server()
        listener = cast(_Listener, self._listener)
        if not listener.done.wait(self.timeout_seconds):
            raise GardenError(
                title="Login timed out",
                detail=f"No approval came back within {int(self.timeout_seconds)} seconds.",
                fix="Run garden login again and approve the login in the browser.",
            )
        if not listener.code:
            raise GardenError(
                title="No code received",
                fix="Run garden login again.",
            )
        return CallbackResult(
            code=listener.code,
            state=self.state,
            nonce=self.nonce,
            verifier=self.challenge.verifier,
            redirect_uri=self.redirect_uri,
            device=self.device,
        )
