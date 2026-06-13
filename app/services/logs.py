"""In-process ring buffer for the bot's own logs.

Why a ring buffer
-----------------

Railway streams ``stdout`` to its log viewer, but accessing it requires
opening Railway. For "is the bot dying right now?" questions we need
something readable from inside Telegram — hence a tiny in-memory buffer
that an admin can dump with ``/logs``.

Design notes
------------

- Standard-library only — uses :class:`collections.deque` with a fixed
  ``maxlen`` so the oldest entries are evicted automatically. No extra
  dependencies.
- Records are stored *formatted* (string), not as ``LogRecord`` objects,
  to keep the buffer small and avoid retaining references to mutable
  state from the rest of the app.
- The handler runs **alongside** stdout — we never replace the existing
  Railway-friendly stdout output, only add a second sink.
- ``snapshot()`` returns a copy so the caller can safely iterate while
  new log lines are being appended from another coroutine.

Usage from ``app/main.py``::

    from app.services.logs import install_ring_buffer_handler

    handler = install_ring_buffer_handler()
    # ... rest of bot startup; handler is also reachable via get_handler()
"""
from __future__ import annotations

import logging
from collections import deque
from threading import Lock
from typing import Iterable

# Number of formatted log lines kept in memory at any time. 500 lines is
# enough to debug a recent issue without bloating RAM (≈100 KB at
# 200 chars/line).
DEFAULT_RING_SIZE = 500

# Default format mirrors :func:`app.main.main`'s basicConfig so lines
# look the same in ``/logs`` output as they do in Railway logs.
DEFAULT_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class RingBufferLogHandler(logging.Handler):
    """Logging handler that keeps the last ``capacity`` formatted records
    in a thread-safe :class:`collections.deque`.

    Reading is done via :meth:`snapshot` (returns a list) or
    :meth:`tail` (returns last N lines, optionally filtered by minimum
    level). Both methods are safe to call from any thread.
    """

    def __init__(self, capacity: int = DEFAULT_RING_SIZE) -> None:
        super().__init__()
        self._buffer: deque[tuple[int, str]] = deque(maxlen=capacity)
        # Buffer mutations happen from arbitrary threads (any logging
        # call site), so a lock is needed even though aiogram itself is
        # single-threaded.
        self._lock = Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001
            # Mirror stdlib Handler.handleError but never re-raise — the
            # whole point of a side-channel handler is that it can't
            # break the app.
            self.handleError(record)
            return
        with self._lock:
            self._buffer.append((record.levelno, line))

    # ----- read API -----

    def snapshot(self) -> list[tuple[int, str]]:
        """Copy of the current buffer, oldest-first."""
        with self._lock:
            return list(self._buffer)

    def tail(
        self, n: int, *, min_level: int = logging.NOTSET
    ) -> list[str]:
        """Last ``n`` lines, optionally filtered by minimum level.

        ``min_level`` follows :mod:`logging` semantics — pass
        :data:`logging.WARNING` to see only warnings and above.
        """
        n = max(1, n)
        items = self.snapshot()
        if min_level > logging.NOTSET:
            items = [(lvl, line) for lvl, line in items if lvl >= min_level]
        return [line for _lvl, line in items[-n:]]

    def clear(self) -> None:
        """Drop every retained line (useful right after diagnosing
        a known issue, so the next ``/logs`` shows only fresh output)."""
        with self._lock:
            self._buffer.clear()


# ---------- module-level singleton helpers ----------

_handler: RingBufferLogHandler | None = None


def install_ring_buffer_handler(
    *,
    capacity: int = DEFAULT_RING_SIZE,
    fmt: str = DEFAULT_FORMAT,
    level: int = logging.NOTSET,
    root_logger: logging.Logger | None = None,
) -> RingBufferLogHandler:
    """Attach a :class:`RingBufferLogHandler` to the root logger.

    Idempotent — calling it twice keeps a single handler installed.
    Returns the handler so callers can inspect it directly if needed
    (mostly for tests).

    Designed to be called once from :func:`app.main.main` AFTER
    ``logging.basicConfig`` so the handler inherits a sane default
    level (and so we don't fight basicConfig's ``force=True`` re-init).
    """
    global _handler

    target = root_logger or logging.getLogger()

    if _handler is not None and _handler in target.handlers:
        return _handler

    handler = RingBufferLogHandler(capacity=capacity)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))

    target.addHandler(handler)
    _handler = handler
    return handler


def get_handler() -> RingBufferLogHandler | None:
    """Return the installed handler, or ``None`` if it was never
    installed (e.g. in unit-test contexts)."""
    return _handler


def get_recent(
    n: int = 50, *, min_level: int = logging.NOTSET
) -> list[str]:
    """Convenience accessor used by the ``/logs`` handler.

    Returns an empty list when the handler was never installed (rather
    than raising) so the UI can show a graceful "no logs yet" message.
    """
    handler = _handler
    if handler is None:
        return []
    return handler.tail(n, min_level=min_level)


# Mapping used by the /logs command to translate user-friendly tokens
# into stdlib log levels. Order matters for the picker keyboard.
LEVEL_TOKENS: list[tuple[str, int, str]] = [
    ("all",     logging.NOTSET,   "Все"),
    ("info",    logging.INFO,     "INFO+"),
    ("warning", logging.WARNING,  "WARN+"),
    ("error",   logging.ERROR,    "ERROR+"),
]


def parse_level(token: str | None) -> int:
    """Map a user-supplied token to a stdlib level. Defaults to NOTSET."""
    if not token:
        return logging.NOTSET
    token = token.lower().strip()
    for key, level, _label in LEVEL_TOKENS:
        if token in {key, key[:1], str(level)}:
            return level
    # Try direct level name (DEBUG, INFO, ...).
    candidate = token.upper()
    if candidate in logging._nameToLevel:  # noqa: SLF001
        return logging._nameToLevel[candidate]  # noqa: SLF001
    return logging.NOTSET


def render_level_label(level: int) -> str:
    """Friendly label for a level number — used in /logs output."""
    if level <= logging.NOTSET:
        return "Все"
    return logging.getLevelName(level)


def render_lines(lines: Iterable[str]) -> str:
    """Format a sequence of log lines for an HTML Telegram message.

    Wraps in ``<pre>``, escapes ``<>&``, and falls back to a sentinel
    when there's nothing to show.
    """
    text = "\n".join(lines).strip()
    if not text:
        return "<i>(пусто)</i>"
    # Telegram requires HTML escaping inside <pre>. Newlines stay as-is.
    escaped = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
    return f"<pre>{escaped}</pre>"
