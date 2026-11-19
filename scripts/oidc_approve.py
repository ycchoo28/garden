"""Complete the local fake OIDC CLI sign-in flow over HTTP without a browser.

Used by automated WSL and Linux acceptance runs so tests do not depend on
Playwright/Chromium system libraries or Windows Edge CDP WebSockets.
"""

from __future__ import annotations

import argparse
import glob
import http.cookiejar
import json
import sys
import time
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

PERSONA_EMAILS: dict[str, str] = {
    "Dev Admin": "dev.admin@fernhill.example",
    "Marcus Lee": "marcus.lee@fernhill.example",
    "Priya Nair": "priya.nair@fernhill.example",
    "Lena Fischer": "lena.fischer@fernhill.example",
}

DEFAULT_PERSONA = "Dev Admin"
DEFAULT_AUTHORIZE_PATH = "/api/agent/cli/authorize"
ALLOWED_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    message: str
    persona_email: str
    authorize_url: str | None = None
    oidc_page_url: str | None = None
    callback_url: str | None = None


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, object]] = []
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self._current = {
                "action": values.get("action"),
                "method": (values.get("method") or "get").lower(),
                "fields": {},
            }
        elif tag == "input" and self._current is not None:
            name = values.get("name")
            if name:
                fields = self._current["fields"]
                assert isinstance(fields, dict)
                fields[name] = values.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def resolve_persona_email(persona: str) -> str:
    if "@" in persona:
        return persona.strip()
    if persona in PERSONA_EMAILS:
        return PERSONA_EMAILS[persona]
    raise ValueError(
        f"Unknown persona {persona!r}. Use an email address or one of: "
        + ", ".join(sorted(PERSONA_EMAILS))
    )


def safe_url_summary(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def validate_authorize_url(url: str) -> str | None:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "http":
        return f"Expected http scheme for local demo authorize URL, got {parsed.scheme!r}."
    if parsed.hostname not in ALLOWED_LOOPBACK_HOSTS:
        return f"Refusing non-loopback authorize host {parsed.hostname!r}."
    if parsed.path != DEFAULT_AUTHORIZE_PATH:
        return f"Expected {DEFAULT_AUTHORIZE_PATH} path, got {parsed.path!r}."
    if not parsed.query:
        return "Authorize URL is missing query parameters."
    return None


def wait_for_login_url(
    *,
    login_link_file: Path | None = None,
    watch_glob: str | None = None,
    created_after: float = 0.0,
    timeout_seconds: float = 300.0,
    poll_interval: float = 0.25,
) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() <= deadline:
        candidates: list[Path] = []
        if login_link_file is not None and login_link_file.is_file():
            candidates.append(login_link_file)
        if watch_glob:
            for match in glob.glob(watch_glob):
                path = Path(match)
                if path.is_file():
                    try:
                        if path.stat().st_mtime >= created_after:
                            candidates.append(path)
                    except OSError:
                        continue
        candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                return text
        time.sleep(poll_interval)
    return None


def approve_login_url(
    login_url: str,
    *,
    persona: str = DEFAULT_PERSONA,
    request_timeout: float = 15.0,
) -> ApprovalResult:
    persona_email = resolve_persona_email(persona)
    url = login_url.strip()
    error = validate_authorize_url(url)
    if error is not None:
        return ApprovalResult(
            ok=False,
            message=error,
            persona_email=persona_email,
            authorize_url=safe_url_summary(url),
        )

    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        page = opener.open(url, timeout=request_timeout)
        page_url = page.geturl()
        html = page.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return ApprovalResult(
            ok=False,
            message=f"Could not load fake OIDC persona page: {type(exc).__name__}: {exc}",
            persona_email=persona_email,
            authorize_url=safe_url_summary(url),
        )

    parser = _FormParser()
    parser.feed(html)
    selected = None
    for form in parser.forms:
        fields = form["fields"]
        assert isinstance(fields, dict)
        if fields.get("username") == persona_email:
            selected = form
            break

    if selected is None:
        return ApprovalResult(
            ok=False,
            message=f"Fake OIDC provider did not offer persona {persona_email}.",
            persona_email=persona_email,
            authorize_url=safe_url_summary(url),
            oidc_page_url=safe_url_summary(page_url),
        )

    action = selected["action"]
    form_url = urljoin(page_url, action if isinstance(action, str) and action else page_url)
    fields = selected["fields"]
    assert isinstance(fields, dict)
    request = Request(
        form_url,
        data=urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        callback = opener.open(request, timeout=request_timeout)
        callback_url = callback.geturl()
        body = callback.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return ApprovalResult(
            ok=False,
            message=f"Fake OIDC callback failed: {type(exc).__name__}: {exc}",
            persona_email=persona_email,
            authorize_url=safe_url_summary(url),
            oidc_page_url=safe_url_summary(page_url),
        )

    callback_path = urlsplit(callback_url).path
    if callback.status != 200 or callback_path != "/callback" or "Sign-in succeeded" not in body:
        return ApprovalResult(
            ok=False,
            message=(
                f"Expected HTTP 200 at /callback with 'Sign-in succeeded', "
                f"reached {safe_url_summary(callback_url)} (status {callback.status})."
            ),
            persona_email=persona_email,
            authorize_url=safe_url_summary(url),
            oidc_page_url=safe_url_summary(page_url),
            callback_url=safe_url_summary(callback_url),
        )

    return ApprovalResult(
        ok=True,
        message=(
            f"Approved {persona_email} via {safe_url_summary(page_url)}; "
            f"CLI received callback at {safe_url_summary(callback_url)}."
        ),
        persona_email=persona_email,
        authorize_url=safe_url_summary(url),
        oidc_page_url=safe_url_summary(page_url),
        callback_url=safe_url_summary(callback_url),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="Direct local demo authorize URL to approve.")
    source.add_argument(
        "--login-link-file",
        type=Path,
        help="Path to the login-link file written by the installer.",
    )
    source.add_argument(
        "--watch-glob",
        help=(
            "Glob pattern to watch for a newly created login-link file "
            "(for example /tmp/garden-demo-*/login-link)."
        ),
    )
    parser.add_argument(
        "--persona",
        default=DEFAULT_PERSONA,
        help=f"Seeded persona name or email (default: {DEFAULT_PERSONA!r}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for the login-link file before timing out (default: 300).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=15.0,
        help="HTTP timeout per request in seconds (default: 15).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the result as JSON.",
    )
    args = parser.parse_args(argv)

    start_wall = time.time() - 1.0
    if args.url:
        login_url = args.url
    else:
        login_url = wait_for_login_url(
            login_link_file=args.login_link_file,
            watch_glob=args.watch_glob,
            created_after=start_wall,
            timeout_seconds=args.timeout,
        )
        if not login_url:
            result = ApprovalResult(
                ok=False,
                message=f"Timed out after {args.timeout:.0f}s waiting for login-link URL.",
                persona_email=resolve_persona_email(args.persona),
            )
            if args.as_json:
                print(json.dumps(asdict(result), indent=2))
            else:
                print(result.message, file=sys.stderr)
            return 1

    result = approve_login_url(
        login_url,
        persona=args.persona,
        request_timeout=args.request_timeout,
    )
    if args.as_json:
        print(json.dumps(asdict(result), indent=2))
    else:
        stream = sys.stdout if result.ok else sys.stderr
        print(result.message, file=stream)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
