"""Session-owned event offsets. Payloads stay in JSONL until a query selects them."""
from contextlib import closing, contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

logger = logging.getLogger("mirofish.replay.index")
_locks = {}
_guard = threading.Lock()
VERSION = 1


@contextmanager
def session_lock(folder):
    with _guard:
        lock = _locks.setdefault(str(folder.resolve()), threading.Lock())
    with lock:
        # Also serialize builders in separate backend worker processes.
        with (folder / "events.lock").open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class EventIndex:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.source = self.folder / "artifacts" / "events.jsonl"
        self.path = self.folder / "events.sqlite3"

    def signature(self):
        stat = self.source.stat()
        return f"{VERSION}:{stat.st_size}:{stat.st_mtime_ns}"

    def valid(self):
        if not self.path.exists():
            return False
        try:
            with closing(self.connect()) as conn:
                return conn.execute("SELECT signature FROM metadata").fetchone() == (self.signature(),)
        except sqlite3.DatabaseError:
            return False

    def connect(self):
        return sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)

    def ensure(self):
        if self.valid():
            return self
        with session_lock(self.folder):
            if self.valid():
                return self
            started = time.monotonic()
            temp = self.path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            count = 0
            try:
                signature = self.signature()
                with closing(sqlite3.connect(temp)) as conn:
                    conn.executescript("""
                        PRAGMA journal_mode=OFF;
                        CREATE TABLE metadata(signature TEXT);
                        CREATE TABLE events(seq INTEGER PRIMARY KEY, offset INTEGER, length INTEGER,
                            stage TEXT, kind TEXT, time REAL, platform TEXT, agent_id,
                            round_num, action TEXT);
                        CREATE TABLE bounds(stage TEXT PRIMARY KEY, start REAL, end REAL);
                    """)
                    with self.source.open("rb") as stream:
                        while True:
                            offset = stream.tell()
                            line = stream.readline()
                            if not line:
                                break
                            event = json.loads(line)
                            data = event["data"] if isinstance(event["data"], dict) else {}
                            conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", (
                                count, offset, len(line), event["stage"], event["kind"], event["time"],
                                data.get("platform"), data.get("agent_id"), data.get("round_num"), data.get("action")))
                            count += 1
                    conn.executescript("""
                        INSERT INTO bounds SELECT stage, MIN(time), MAX(time) FROM events
                            WHERE NOT (stage='simulation' AND kind='graph') GROUP BY stage;
                        CREATE INDEX stage_kind_order ON events(stage, kind, seq);
                        CREATE INDEX action_filter ON events(stage, kind, platform, agent_id, round_num);
                    """)
                    if self.signature() != signature:
                        raise ValueError("Replay event history changed while indexing")
                    conn.execute("INSERT INTO metadata VALUES (?)", (signature,))
                    conn.commit()
                os.replace(temp, self.path)
                logger.info("Replay indexed replay_id=%s events=%d duration=%.3fs", self.folder.name, count, time.monotonic() - started)
            except Exception as exc:
                # Exception messages may contain source content; log only their type.
                logger.error("Replay indexing failed replay_id=%s error_type=%s", self.folder.name, type(exc).__name__)
                raise
            finally:
                temp.unlink(missing_ok=True)
        return self

    def query(self, stage, kind, position, *, filters=None, offset=0, limit=None, reverse=False, count=False):
        self.ensure()
        with closing(self.connect()) as conn:
            bounds = conn.execute("SELECT start, end FROM bounds WHERE stage=?", (stage,)).fetchone()
            if bounds is None:
                return 0 if count else []
            clauses, params = ["stage=?"], [stage]
            if kind is not None:
                clauses.append("kind=?")
                params.append(kind)
            if position < 1:
                clauses.extend(["time<=?", "kind!='completed'",
                                "NOT (stage='report' AND kind='agent_log' AND action IS 'report_complete')"])
                params.append(bounds[0] + (bounds[1] - bounds[0]) * position)
            for key, value in (filters or {}).items():
                if key not in {"platform", "agent_id", "round_num"}:
                    raise ValueError("Invalid event filter")
                clauses.append(key + "=?")
                params.append(value)
            where = " AND ".join(clauses)
            if count:
                return conn.execute("SELECT COUNT(*) FROM events WHERE " + where, params).fetchone()[0]
            sql = "SELECT offset, length FROM events WHERE " + where + " ORDER BY seq " + ("DESC" if reverse else "ASC")
            sql += " LIMIT ? OFFSET ?"
            params.extend([-1 if limit is None else limit, offset])
            # Fetch only offsets selected by SQL. Iteration keeps unpaginated queries bounded.
            with self.source.open("rb") as stream:
                return list(self._payloads(stream, conn.execute(sql, params)))

    @staticmethod
    def _payloads(stream, rows):
        for offset, length in rows:
            stream.seek(offset)
            yield json.loads(stream.read(length))["data"]
