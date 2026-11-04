"""`garden login`: the browser approves the login, and the code comes back to a listener on this
machine.

The CLI starts the sign-in at the portal with a PKCE challenge and a loopback redirect. The browser
signs in with the identity provider, which redirects to the listener with a one-time code and the
state this process sent. The CLI then trades the code, with its PKCE verifier and nonce, for a CLI
login token, which the portal shows only once.
"""

from __future__ import annotations

import html
import secrets
import socket
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

from garden.browser import open_link
from garden.errors import GardenError
from garden.jsonio import as_object, as_text
from garden.pkce import challenge_for, new_verifier
from garden.portal import PortalClient

AUTHORIZE_PATH = "/api/cli-logins/authorize"
CREATE_PATH = "/api/cli-logins"
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
class Login:
    token: str
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


def run_login(
    portal_url: str,
    device: str,
    *,
    open_browser: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Login:
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = new_verifier()
    listener = _Listener(state, device)
    redirect_uri = f"http://127.0.0.1:{listener.server_address[1]}{CALLBACK_PATH}"
    url = authorize_url(
        portal_url,
        redirect_uri=redirect_uri,
        state=state,
        nonce=nonce,
        challenge=challenge_for(verifier),
    )
    # Flushed at once: a person may be waiting on this link while the listener runs.
    print(f"Approve this login for {device} in your browser:", flush=True)
    print(f"  {url}", flush=True)
    if open_browser and not open_link(url):
        print(
            "No browser could be opened here. Open the link above yourself to approve the login.",
            flush=True,
        )
    # A short poll interval: shutdown() waits for one poll, and the default 0.5 s is paid per login.
    worker = threading.Thread(
        target=lambda: listener.serve_forever(poll_interval=0.05), daemon=True
    )
    worker.start()
    try:
        if not listener.done.wait(timeout):
            raise GardenError(
                title="Login timed out",
                detail=f"No approval came back within {int(timeout)} seconds.",
                fix="Run garden login again and approve the login in the browser.",
            )
    finally:
        listener.shutdown()
        listener.server_close()
    return _create(
        portal_url,
        code=listener.code or "",
        verifier=verifier,
        redirect_uri=redirect_uri,
        nonce=nonce,
        device=device,
    )


def _create(
    portal_url: str,
    *,
    code: str,
    verifier: str,
    redirect_uri: str,
    nonce: str,
    device: str,
) -> Login:
    reply = PortalClient(portal_url).request(
        "POST",
        CREATE_PATH,
        body={
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "nonce": nonce,
            "device": device,
        },
    )
    token = as_text(as_object(reply.body).get("token"))
    if not token:
        raise GardenError(title="The portal sent no login", fix="Run garden login again.")
    return Login(token=token, device=device)


def device_name() -> str:
    try:
        return socket.gethostname() or "this-device"
    except OSError:
        return "this-device"
