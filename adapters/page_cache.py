"""Layer: Adapters — on-disk cache of table-reader answers, one file per page.

The key is a SHA-256 of everything that determines the answer: the page image
bytes, the model, the effort, the prompt and the output schema.  The same page
of the same file therefore costs one model call, ever — a retry or a re-upload
is answered from disk in milliseconds and gives the identical reading (audit
results are reproducible).  Changing the model, the prompt or the schema changes
the key, so a stale answer is never reused.

Cache I/O problems never fail a job: a read error is a miss, a write error is
logged and ignored.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class PageCache:
    def __init__(self, directory: str | os.PathLike) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: bytes | str) -> str:
        h = hashlib.sha256()
        for part in parts:
            data = part.encode("utf-8") if isinstance(part, str) else part
            h.update(len(data).to_bytes(8, "big"))      # unambiguous concatenation
            h.update(data)
        return h.hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> str | None:
        try:
            return self._path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            log.warning("Page cache read failed (%s) — reading the page again", exc)
            return None

    def put(self, key: str, text: str) -> None:
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)                        # atomic: never a half file
        except OSError as exc:
            log.warning("Page cache write failed (%s) — continuing without cache", exc)
