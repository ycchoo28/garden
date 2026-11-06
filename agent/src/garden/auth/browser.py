"""Open the approval link in the person's browser without letting the opener talk on the terminal.

On Linux the standard opener is `gio open`, which prints "Operation not supported" to stderr when no
handler is set up. That child process writes straight to the file descriptors it inherits, so the
noise is silenced at the descriptor level for the duration of the call, and the link is printed
by the caller instead.
"""

from __future__ import annotations

import os
import webbrowser
from collections.abc import Callable


def open_link(url: str, opener: Callable[[str], bool] | None = None) -> bool:
    """Ask the browser to open the link. True only when an opener reported it opened the link.

    Anything the opener writes to stdout or stderr goes to the null device, and the descriptors are
    restored afterwards, even when the opener raises.
    """
    open_with = opener if opener is not None else webbrowser.open
    try:
        saved = [os.dup(fd) for fd in (1, 2)]
    except OSError:
        # No usable standard streams to protect, so there is nothing to hide either.
        return _attempt(url, open_with)
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
        return _attempt(url, open_with)
    finally:
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, null):
            os.close(fd)


def _attempt(url: str, open_with: Callable[[str], bool]) -> bool:
    try:
        return bool(open_with(url))
    except webbrowser.Error:
        return False
