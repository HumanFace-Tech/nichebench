"""Minimal SQLite session-store archive for capture.

Two guarantees the existing trajectory reconstruction does not provide:

1. **All sessions, all rows.** ``build_trajectory_from_sqlite`` keeps only the
   newest session and reduces parts to a summary. Capture exports every table
   from every session, preserving OpenCode's own IDs and raw JSON.
2. **WAL-consistent backups.** SQLite in WAL mode keeps recent commits in
   ``-wal``/``-shm`` sidecars; copying only the ``.db`` file loses them. The
   backup API is used so the copy includes a consistent view of the WAL.

Archived DBs are copies. The live DB directory stays mounted into the cage; the
archive itself is never mounted, so the agent cannot rewrite history.

Ownership: DB archiving only. Journal writes live in :mod:`store`.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from nichebench.execution.runtime.capture.errors import CaptureWriteError
from nichebench.execution.runtime.capture.run import ensure_private_dir, write_private_json
from nichebench.execution.runtime.capture.scrub import (
    StreamingCredentialScrubber,
    normalise_known,
    scrub_bytes,
    scrub_known_credentials,
)

DB_FILENAME = "opencode.db"

__all__ = [
    "DB_FILENAME",
    "archive_session_state",
    "consistent_backup",
    "decode_column",
    "encode_column",
    "export_tables",
    "find_db",
    "normalize_agent_timeline",
    "scrub_row",
    "scrub_sqlite_file",
]


def find_db(data_dir: Optional[Path]) -> Optional[Path]:
    """Locate the live ``opencode.db`` under an XDG data directory.

    Args:
        data_dir: Host path of the cage ``XDG_DATA_HOME``.

    Returns:
        The database path, or ``None`` when no store exists yet. The lookup is
        deliberately narrow — the conventional path plus one level of ``*``
        globbing — so a large directory tree is never walked.
    """
    if data_dir is None:
        return None
    base = Path(data_dir)
    candidate = base / "opencode" / DB_FILENAME
    if candidate.exists():
        return candidate
    matches = sorted(base.glob(f"*/{DB_FILENAME}"))
    return matches[0] if matches else None


def consistent_backup(
    db_path: Path,
    dest_dir: Path,
    known_credentials: Optional[Iterable[str]] = None,
    reserve_bytes: Optional[Callable[[int], None]] = None,
) -> Optional[Path]:
    """Back up the live DB into ``dest_dir`` using SQLite's backup API.

    Args:
        db_path: Live ``opencode.db`` inside the cage state directory.
        dest_dir: Destination directory (created private).

    Returns:
        Path to the backup file, or ``None`` when the source is unreadable.
    """
    if not db_path.exists():
        return None
    ensure_private_dir(dest_dir)
    credentials = list(known_credentials or [])
    # Raw SQLite pages (including freed pages) must never enter the archive.
    # Build and scrub in private temporary storage, then publish only a safe copy.
    with tempfile.TemporaryDirectory(prefix="nichebench-db-backup-") as scratch:
        staging = _backup_to(db_path, Path(scratch) / DB_FILENAME)
        if staging is None:
            return None
        redaction = scrub_sqlite_file(staging, credentials)
        if redaction.get("error"):
            return None
        scanner = StreamingCredentialScrubber(credentials)
        with staging.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                scanner.feed(chunk)
                if scanner.redacted:
                    return None
        scanner.flush()
        if scanner.redacted:
            return None
        dest = dest_dir / DB_FILENAME
        if reserve_bytes is not None:
            reserve_bytes(staging.stat().st_size)
        # Temporary storage and results can be on different filesystems.
        import shutil

        publish = dest.with_suffix(".safe-staging")
        try:
            shutil.copyfile(staging, publish)
            os.chmod(publish, 0o600)
            os.replace(publish, dest)
        finally:
            with contextlib.suppress(OSError):
                publish.unlink()
        return dest


def _backup_to(db_path: Path, dest: Path) -> Optional[Path]:
    """Copy ``db_path`` to ``dest`` atomically, including WAL content.

    The backup runs against a staging file and is then renamed into place, so a
    refresh never leaves a half-written snapshot for a reader.
    """
    staging = dest.with_name(dest.name + ".staging")
    with contextlib.suppress(OSError):
        staging.unlink()
    try:
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        out = sqlite3.connect(staging)
        try:
            src.backup(out)
        finally:
            out.close()
        os.replace(staging, dest)
        return dest
    except sqlite3.Error as exc:
        with contextlib.suppress(OSError):
            staging.unlink()
        # Runtime constants exist on supported Python; some external SQLite
        # stubs omit the error-code constants introduced in Python 3.11.
        storage_errors = (getattr(sqlite3, "SQLITE_FULL"), getattr(sqlite3, "SQLITE_IOERR"))
        if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) in storage_errors:
            raise CaptureWriteError("capture SQLite backup storage failed") from exc
        return None
    finally:
        src.close()


def _table_names(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [str(row[0]) for row in cur.fetchall()]


def encode_column(value: Any) -> Dict[str, Any]:
    """Encode one SQLite cell losslessly.

    A ``BLOB`` column is base64 here rather than decoded as text, and one that
    is not valid UTF-8 is tagged rather than lossily replaced — otherwise a raw
    session row can differ from what was stored, and the "raw JSON" claim stops
    being true.
    """
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": repr(value)}
    if isinstance(value, bytes):
        return {"type": "blob", "encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, memoryview):
        raw = value.tobytes()
        return {"type": "blob", "encoding": "base64", "value": base64.b64encode(raw).decode("ascii")}
    text = str(value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return {
            "type": "text_lossy",
            "encoding": "base64",
            "value": base64.b64encode(text.encode("utf-8", "surrogatepass")).decode("ascii"),
        }
    return {"type": "text", "value": text}


def decode_column(encoded: Any) -> Any:
    """Inverse of :func:`encode_column`."""
    if not isinstance(encoded, dict):
        return encoded
    kind = encoded.get("type")
    raw = encoded.get("value")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if kind == "null":
        return None
    if kind in ("int",):
        return int(raw) if raw is not None else None
    if kind == "float":
        return float(raw) if raw is not None else None
    if kind == "bool":
        return bool(raw)
    if kind in ("blob", "text_lossy"):
        if encoded.get("encoding") == "base64" and raw is not None:
            return base64.b64decode(raw)
        return raw
    return raw


def scrub_row(row: Dict[str, Any], known: Iterable[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Replace known credential literals inside an exported row.

    Works on the encoded form so a credential inside a blob or a lossily
    encoded column is still caught.
    """
    hits: List[str] = []
    out: Dict[str, Any] = {}
    for key, encoded in row.items():
        if not isinstance(encoded, dict):
            out[key] = encoded
            continue
        if encoded.get("type") in ("blob", "text_lossy"):
            raw = decode_column(encoded)
            if isinstance(raw, bytes):
                scrubbed, found = scrub_bytes(raw, known)
                if found:
                    hits.extend(found)
                    out[key] = encode_column(scrubbed)
                    continue
            out[key] = encoded
            continue
        value = encoded.get("value")
        if isinstance(value, str):
            clean, found = scrub_known_credentials(value, known)
            if found:
                hits.extend(found)
                out[key] = {**encoded, "value": clean}
                continue
        out[key] = encoded
    return out, hits


def export_tables(
    db_path: Path,
    journal: Any,
    max_rows_per_table: Optional[int] = None,
    known_credentials: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Stream every table of ``db_path`` into the capture journal as blob refs.

    Each cell is encoded losslessly (see :func:`encode_column`) so BLOB and
    non-UTF-8 columns survive export intact, and known credentials are replaced
    before the row is written.

    Args:
        db_path: SQLite file to read (backup or live read-only).
        journal: Attempt journal.
        max_rows_per_table: Optional cap used by tests. Production passes
            ``None`` — the contract is "all sessions, all rows".
        known_credentials: Literal credentials to replace in exported rows.

    Returns:
        Summary dict with per-table row counts, or ``{"error": ...}``.
    """
    if not db_path.exists():
        return {"error": "db_missing", "path": str(db_path)}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error as exc:
        return {"error": "db_open_failed", "detail": str(exc)}
    summary: Dict[str, Any] = {"tables": {}, "rows_redacted": 0}
    try:
        conn.row_factory = sqlite3.Row
        for table in _table_names(conn):
            count_row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
            total = int(count_row[0]) if count_row else 0
            emitted = 0
            query = f'SELECT * FROM "{table}"'
            if max_rows_per_table is not None:
                query += f" LIMIT {int(max_rows_per_table)}"
            for row in conn.execute(query):
                # sqlite3.Row exposes column names via .keys(); ``in`` on that
                # view is the documented membership check.
                encoded = {key: encode_column(row[key]) for key in row.keys()}  # noqa: SIM118
                payload, hits = scrub_row(encoded, known_credentials or [])
                if hits:
                    summary["rows_redacted"] += 1
                identity_source = {k: decode_column(v) for k, v in payload.items()}
                journal.store_payload(
                    f"db_row:{table}",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    extra={
                        "event": "db_row",
                        "boundary": "db_snapshot",
                        "source": "session_db",
                        "table": table,
                        "cell_encoding": "typed",
                        "exact": not hits,
                        "exclusions": ["known_credential_replaced"] if hits else [],
                        # Stable per-row identity so a reader can fold rows
                        # back into their session without re-parsing the JSON.
                        "row_identity": {
                            key: (payload[key] if isinstance(identity_source[key], bytes) else identity_source[key])
                            for key in ("id", "session_id", "message_id")
                            if key in identity_source
                        },
                    },
                )
                emitted += 1
            summary["tables"][table] = {"rows_total": total, "rows_exported": emitted}
    except sqlite3.Error as exc:
        summary["error"] = "db_export_failed"
        summary["detail"] = str(exc)
    finally:
        conn.close()
    return summary


def normalize_agent_timeline(db_path: Path) -> List[Dict[str, Any]]:
    """Build a normalised, chronological agent timeline from the DB.

    Ordering semantics are explicit: ``time_created`` ascending, ties broken by
    the message/part id string. This is a per-attempt ordering derived from
    OpenCode's own timestamps — **not** a global order across sessions or
    sources, and not a claim about provider-side event order.
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    events: List[Dict[str, Any]] = []
    try:
        conn.row_factory = sqlite3.Row
        tables = set(_table_names(conn))
        if not {"message", "part"}.issubset(tables):
            return events
        rows = conn.execute(
            "SELECT m.id AS message_id, m.session_id AS session_id, m.time_created AS message_time, "
            "m.data AS message_data, p.id AS part_id, p.time_created AS part_time, p.data AS part_data "
            "FROM message m LEFT JOIN part p ON p.message_id = m.id "
            "ORDER BY m.time_created ASC, m.id ASC, p.time_created ASC, p.id ASC"
        ).fetchall()
        for index, row in enumerate(rows):
            message_data = _load_json(row["message_data"])
            part_data = _load_json(row["part_data"])
            events.append(
                {
                    "order": index,
                    "message_id": row["message_id"],
                    "session_id": row["session_id"],
                    "part_id": row["part_id"],
                    "message_time": row["message_time"],
                    "part_time": row["part_time"],
                    "role": message_data.get("role"),
                    "finish": message_data.get("finish"),
                    "part_type": part_data.get("type") if isinstance(part_data, dict) else None,
                    "part_state": part_data.get("state") if isinstance(part_data, dict) else None,
                    "ordering_basis": "message.time_created, message.id, part.time_created, part.id",
                }
            )
    except sqlite3.Error:
        return events
    finally:
        conn.close()
    return events


def _load_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def scrub_sqlite_file(db_path: Path, known: Iterable[str]) -> Dict[str, Any]:
    """Replace known credential literals inside a SQLite snapshot.

    Session rows can contain credential echoes in tool output or configuration.
    Every text/blob cell of every
    table is rewritten with the literal values removed. This mutates the copy in
    place — the live DB is never touched.

    Returns:
        Summary dict with ``redacted_cells`` and ``scanned_tables``.
    """
    credentials = [c for c in normalise_known(known)]
    summary: Dict[str, Any] = {"redacted_cells": 0, "scanned_tables": [], "credentials_present": bool(credentials)}
    if not credentials or not db_path.exists():
        return summary
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return summary
    try:
        conn.row_factory = sqlite3.Row
        for table in _table_names(conn):
            summary["scanned_tables"].append(table)
            quoted_table = '"' + table.replace('"', '""') + '"'
            # Primary-key predicates also cover WITHOUT ROWID tables. Internal
            # SQLite tables cannot be updated safely: withhold if they contain a
            # known literal rather than claiming a sanitized copy.
            columns = conn.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            primary = [str(c[1]) for c in sorted(columns, key=lambda c: c[5]) if c[5]]
            select = (
                f"SELECT * FROM {quoted_table}" if primary else f"SELECT rowid AS __nb_rowid__, * FROM {quoted_table}"
            )
            cursor = conn.execute(select)
            while batch := cursor.fetchmany(128):
                for row in batch:
                    _scrub_sqlite_row(conn, quoted_table, row, primary, credentials, summary)
        conn.commit()
        # Erase free pages left by updates, or secrets remain in the raw file.
        conn.execute("VACUUM")
    except sqlite3.Error:
        summary["error"] = "sqlite_scrub_failed"
    finally:
        conn.close()
    return summary


def _scrub_sqlite_row(
    conn: sqlite3.Connection,
    quoted_table: str,
    row: sqlite3.Row,
    primary: List[str],
    credentials: List[str],
    summary: Dict[str, Any],
) -> None:
    for key in row.keys():  # noqa: SIM118 - sqlite3.Row exposes names via .keys()
        if key == "__nb_rowid__":
            continue
        value = row[key]
        clean: Any = value
        if isinstance(value, str):
            scrubbed, hits = scrub_known_credentials(value, credentials)
            if hits:
                clean = scrubbed
        elif isinstance(value, (bytes, bytearray)):
            scrubbed_bytes, hits = scrub_bytes(bytes(value), credentials)
            if hits:
                clean = sqlite3.Binary(scrubbed_bytes)
        else:
            continue
        if hits:
            summary["redacted_cells"] += 1
            names = primary or ["rowid"]
            predicate = " AND ".join('"' + n.replace('"', '""') + '" IS ?' for n in names)
            params = [row[n] for n in primary] if primary else [row["__nb_rowid__"]]
            quoted_key = '"' + key.replace('"', '""') + '"'
            conn.execute(f"UPDATE {quoted_table} SET {quoted_key} = ? WHERE {predicate}", [clean, *params])


def archive_session_state(
    data_dir: Optional[Path],
    attempt_dir: Path,
    journal: Any,
    max_rows_per_table: Optional[int] = None,
    known_credentials: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Back up, scrub, and export the OpenCode session store for one attempt.

    Args:
        data_dir: Host path of the cage ``XDG_DATA_HOME`` (live DB location).
        attempt_dir: This attempt's directory (backup destination).
        journal: Attempt journal.
        max_rows_per_table: Optional row cap for tests.
        known_credentials: Literal credentials removed from the snapshot and the
            exported rows.

    Returns:
        Summary dict including ``backup`` path, table counts, timeline path, the
        explicit ``unsent_vs_sent`` note, and the redaction summary.
    """
    summary: Dict[str, Any] = {
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "unsent_vs_sent": (
            "Tool output written by the agent but never transmitted upstream remains "
            "only in the session store and in file payloads; it is present here and "
            "absent from the captured HTTP request bodies."
        ),
    }
    db_path = find_db(data_dir)
    if db_path is None:
        summary["error"] = "db_missing"
        journal.append(
            {"event": "db_snapshot_skipped", "reason": "db_missing"},
            boundary="db_snapshot",
            source="session_db",
        )
        return summary

    backup = consistent_backup(
        db_path,
        attempt_dir / "opencode-db",
        known_credentials=known_credentials,
        reserve_bytes=lambda size: journal.reserve_file(attempt_dir / "opencode-db" / DB_FILENAME, size),
    )
    if backup is None:
        summary["error"] = "backup_failed"
        journal.append(
            {"event": "db_snapshot_failed", "stage": "backup"},
            boundary="db_snapshot",
            source="session_db",
        )
    else:
        summary["backup"] = str(backup)
        redaction = {"sanitised_before_publish": True, "known_credentials_configured": bool(known_credentials)}
        summary["snapshot_redaction"] = redaction
        journal.append(
            {
                "event": "db_snapshot_scrubbed",
                "exact": not known_credentials,
                "exclusions": ["snapshot_sanitised"] if known_credentials else [],
                "scope": "every text and blob cell in the copied snapshot",
            },
            boundary="db_snapshot",
            source="session_db",
        )

    read_from = backup or db_path
    summary["export"] = export_tables(
        read_from,
        journal,
        max_rows_per_table=max_rows_per_table,
        known_credentials=known_credentials,
    )

    timeline = normalize_agent_timeline(read_from)
    # A withheld backup may fall back to the live read-only DB for row export.
    # Protect the derived timeline too, including full pending tool state.
    timeline_text, timeline_hits = scrub_known_credentials(
        json.dumps(timeline, ensure_ascii=False),
        known_credentials or [],
    )
    timeline = json.loads(timeline_text)
    if timeline:
        journal.reserve_file(attempt_dir / "agent_timeline.json", len(timeline_text.encode("utf-8")))
        write_private_json(attempt_dir / "agent_timeline.json", timeline)
        summary["agent_timeline"] = str(attempt_dir / "agent_timeline.json")
        summary["agent_timeline_events"] = len(timeline)
        journal.append(
            {
                "event": "agent_timeline_exported",
                "path": "agent_timeline.json",
                "events": len(timeline),
                "exact": not timeline_hits,
                "ordering_basis": "message.time_created, message.id, part.time_created, part.id",
            },
            boundary="agent_timeline",
            source="session_db",
        )
    return summary
