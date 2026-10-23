"""A self-describing JSONL run log.

The ledger opens with a format descriptor, each series declares its schema
on first use, every record carries its series and capture time, and the log
closes with a random-access index. A reader with no prior knowledge can
still audit the run. Accepts a path string, a file-like object, or nothing
— the default is an in-memory buffer.

A path opens in append mode by default: reopening an existing ledger keeps
every earlier run and starts a new self-describing segment (format
descriptor, series descriptors, records, closing index). Pass ``mode="w"``
to truncate deliberately.

Every line is flushed as it is written, so a record survives the writing
process crashing. ``close()`` also fsyncs a file the ledger opened itself.
"""

import io
import json
import os
import threading
from datetime import UTC, datetime
from typing import Any, TextIO

FORMAT = "interlock-ledger"
VERSION = "1.0.0"
MODES = ("a", "w")


class Ledger:
    """Append-only JSONL log with format descriptor, series descriptors, and a closing index.

    Appends and close are serialised with an internal lock, so one ledger
    may be shared across threads: concurrent first appends to a series
    write exactly one series descriptor, and the closing index counts
    every record actually written.
    """

    def __init__(self, dest: str | TextIO | None = None, mode: str = "a") -> None:
        """Open a ledger.

        ``mode`` applies only when ``dest`` is a path: ``"a"`` (default) appends
        a new segment and preserves earlier runs; ``"w"`` truncates the file.
        """
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self._path: str | None = None
        self._owns_handle = False
        if dest is None:
            self._buf: TextIO = io.StringIO()
        elif isinstance(dest, str):
            self._path = dest
            self._buf = open(dest, mode, encoding="utf-8")  # noqa: SIM115 -- owned, closed by close()
            self._owns_handle = True
        else:
            self._buf = dest
        self._series: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._lock = threading.Lock()
        self._write({"_type": "format", "format": FORMAT, "version": VERSION})

    def _write(self, obj: dict[str, Any]) -> None:
        # Flush every line: a record buffered in-process dies with the process.
        self._buf.write(json.dumps(obj) + "\n")
        self._buf.flush()

    def _lines(self) -> list[str]:
        if self._path is not None:
            try:
                self._buf.flush()
            except ValueError:
                pass  # handle closed by close(); the file on disk is complete
            with open(self._path, encoding="utf-8") as handle:
                return handle.read().splitlines()
        self._buf.flush()
        self._buf.seek(0)
        lines = self._buf.read().splitlines()
        self._buf.seek(0, io.SEEK_END)
        return lines

    def append(self, series: str, record: dict[str, Any]) -> dict[str, Any]:
        """Append a record to a series, stamping it with ``_series`` and ``_ts``.

        The first append to a new series writes a series descriptor declaring
        the schema (the record's sorted keys). Returns the stamped record.
        """
        with self._lock:
            if self._closed:
                raise ValueError("ledger is closed")
            if series not in self._series:
                self._write({"_type": "series", "series": series, "schema": sorted(record.keys())})
                self._series[series] = {"count": 0, "first_ts": None, "last_ts": None}
            entry = dict(record)
            entry["_type"] = "record"
            entry["_series"] = series
            entry["_ts"] = datetime.now(UTC).isoformat()
            info = self._series[series]
            info["count"] += 1
            info["last_ts"] = entry["_ts"]
            if info["first_ts"] is None:
                info["first_ts"] = entry["_ts"]
            self._write(entry)
            return entry

    def close(self) -> None:
        """Append the closing index. Idempotent: closing twice writes one index."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            index = {name: dict(info) for name, info in self._series.items()}
            self._write({"_type": "index", "series": index})
            if self._owns_handle:
                os.fsync(self._buf.fileno())  # one fsync per run: the closed file is on disk
                self._buf.close()

    def read_series(self, series: str):
        """Yield the record dicts of one series, in append order.

        For a path-backed ledger this spans every segment in the file,
        earlier runs included.
        """
        for line in self._lines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("_type") == "record" and obj.get("_series") == series:
                yield obj
