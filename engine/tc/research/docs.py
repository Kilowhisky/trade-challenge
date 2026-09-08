"""The six documents Claude reads whole, and the checks that stand between a
truncated heredoc and the file the next pass will trust.

Every rule here is a v2 writer's rule, ported by number rather than by reading
the bash: research-write.sh's four validators plus its compare-and-swap on the
`Last pass:` line, and research-replace.sh's per-target H1 and banner table
(0c-writers-contract.md §1.1, §1.2).

Two things deliberately did NOT survive the port:

* **The mkdir lock and its exit 5.** v2 needed advisory locking because any
  number of bash processes could write the same file; here there is exactly one
  writer process (spec §2.3) and an asyncio.Lock per kind is the whole story.
  The CAS is still taken inside that lock, so compare-and-swap stays atomic.
* **Numeric exit codes.** A caller had to map 1/3/5 back to meaning. Two
  exception types say it directly, and they are different because the response
  is different: a validation error means fix the body; a CAS mismatch means
  re-read, merge, and retry exactly once, never with the stale copy.

What was *added*: the write itself is atomic (tmp + fsync + rename + directory
fsync, as tc/broker/token.py writes the token). v2's `printf > file` truncated
the live document first, so a crash mid-write left the next pass reading half a
file that still passed no validator -- these documents are read whole by a
process that cannot ask a human what happened.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tc.store.db import Store

DocKind = Literal["candidates", "standing", "scorecard", "preopen", "options-roster", "universe"]

# Anchored, as v2's `grep '^Last pass:'` was: a "Last pass:" quoted mid-sentence
# inside the body is prose, not the document's stamp.
LAST_PASS = re.compile(r"^Last pass:.*$", re.MULTILINE)
VERIFIED_AS_OF = re.compile(r"^Verified as of:.*$", re.MULTILINE)


class DocValidationError(ValueError):
    """The body is not the document it claims to be."""


# N818 wants an "Error" suffix. The name is the contract the MCP tools and the
# runner catch by (task-4-brief.md), and "CasMismatchError" reads as a failure
# of the CAS rather than what this is: the CAS working, and the caller holding
# a stale copy.
class DocCasMismatch(ValueError):  # noqa: N818
    """Someone else wrote since you read. Re-read, merge, retry once."""


@dataclass(frozen=True)
class _Spec:
    filename: str            # "{date}" is substituted for a dated kind
    min_lines: int           # the body must have MORE lines than this
    first_line: str          # "{date}" likewise
    banners: tuple[str, ...]
    dated: bool
    needs_last_pass: bool = False
    needs_verified: bool = False


SPECS: dict[str, _Spec] = {
    "candidates": _Spec(
        "candidates.md", 10, "# Research candidates",
        ("never a source for order parameters",), False, needs_last_pass=True,
    ),
    "options-roster": _Spec(
        "options-roster.md", 5, "# Options-viable roster",
        ("never a source for order parameters", "TTL"), False,
    ),
    "preopen": _Spec(
        "preopen-{date}.md", 5, "# Pre-open brief — {date}",
        ("Pre-market data informs, it never qualifies",), True,
    ),
    "scorecard": _Spec(
        "scorecard.md", 5, "# Research scorecard",
        ("never loosens a gate in-flight", "explicit conversation with Chris"), False,
    ),
    "universe": _Spec(
        "universe.md", 5, "# Fallback universe",
        ("never a source for order parameters",), False,
    ),
    "standing": _Spec(
        "standing.md", 5, "# Standing research reference",
        ("never a source for order parameters",), False, needs_verified=True,
    ),
}


class DocView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: DocKind
    path: str
    exists: bool
    body: str
    last_pass: str | None
    verified_as_of: str | None


class DocWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: DocKind
    path: str
    lines: int


class DocStore:
    def __init__(self, root: Path, store: Store) -> None:
        self.root = root
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    @staticmethod
    def _spec(kind: DocKind) -> _Spec:
        try:
            return SPECS[kind]
        except KeyError:
            # The kind arrives from an MCP argument, so an unknown one is a
            # caller's mistake to report, not a KeyError to leak.
            raise DocValidationError(f"unknown document kind {kind!r}") from None

    def path_for(self, kind: DocKind, d: date | None) -> Path:
        spec = self._spec(kind)
        if spec.dated:
            if d is None:
                raise DocValidationError(f"{kind} needs a date")
            return self.root / spec.filename.format(date=d.isoformat())
        if d is not None:
            raise DocValidationError(f"{kind} takes no date")
        return self.root / spec.filename

    # --- file I/O, kept synchronous and off the async call sites -----------
    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text() if path.exists() else ""

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """Write through a tmp file so the document is never half-replaced.

        The rename is atomic; the two fsyncs are what make it survive a power
        loss -- the data before the rename, the directory after it, or the
        rename can be lost and leave an empty file where a document was.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    async def read(self, kind: DocKind, d: date | None = None) -> DocView:
        path = self.path_for(kind, d)          # raises on a date/arity mismatch
        body = self._read(path)
        lp = LAST_PASS.search(body)
        va = VERIFIED_AS_OF.search(body)
        return DocView(
            kind=kind, path=str(path), exists=path.exists(), body=body,
            last_pass=lp.group(0).strip() if lp else None,
            verified_as_of=va.group(0).strip() if va else None,
        )

    def _validate(self, kind: DocKind, body: str, d: date | None) -> None:
        spec = self._spec(kind)
        lines = body.splitlines()
        if len(lines) <= spec.min_lines:
            raise DocValidationError(
                f"{kind}: {len(lines)} lines, need more than {spec.min_lines}"
                " (truncated heredoc?)"
            )
        want = spec.first_line
        if spec.dated:
            if d is None:  # unreachable through replace(): path_for refuses it first
                raise DocValidationError(f"{kind} needs a date")
            want = want.format(date=d.isoformat())
        if lines[0].strip() != want:
            raise DocValidationError(f"{kind}: first line must be exactly {want!r}")
        for banner in spec.banners:
            if banner not in body:
                raise DocValidationError(f"{kind}: body must contain {banner!r}")
        if spec.needs_last_pass and not LAST_PASS.search(body):
            raise DocValidationError(f"{kind}: body must carry a line starting 'Last pass:'")
        if spec.needs_verified and not VERIFIED_AS_OF.search(body):
            raise DocValidationError(f"{kind}: body must carry a line starting 'Verified as of:'")

    async def replace(
        self,
        kind: DocKind,
        body: str,
        d: date | None = None,
        expect_last_pass: str | None = None,
    ) -> DocWrite:
        path = self.path_for(kind, d)          # raises on a date/arity mismatch
        self._validate(kind, body, d)
        text = body if body.endswith("\n") else body + "\n"
        lines = len(text.splitlines())
        async with self._lock(str(path)):
            if expect_last_pass is not None and path.exists():
                # No CAS against a file that does not exist: v2 skipped the
                # compare in that case too, and the first writer of a document
                # has nothing to be stale against.
                current = LAST_PASS.search(self._read(path))
                have = current.group(0).strip() if current else None
                if have != expect_last_pass.strip():
                    raise DocCasMismatch(
                        f"{kind} moved under you: expected {expect_last_pass!r}, found {have!r}."
                        " Re-read, merge onto the fresh copy, retry once."
                    )
            if path.exists():
                # The snapshot is taken only once the write is certain to
                # happen, so a refused body never costs the operator the one
                # copy of the previous version.
                self._write_atomic(path.with_name(path.name + ".prev"), self._read(path))
            self._write_atomic(path, text)
            await self.store.record_artifact(
                kind, d, str(path), hashlib.sha256(text.encode()).hexdigest(), lines
            )
        return DocWrite(kind=kind, path=str(path), lines=lines)
