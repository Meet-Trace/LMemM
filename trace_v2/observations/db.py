"""SQLite connection and schema migrations for the V2 Observation store.

Contract:
    open_database(path) -> sqlite3.Connection, migrated to LATEST_VERSION.
    - The file is created owner-only (0600) in an owner-only (0700) folder.
    - foreign_keys=ON, WAL journal, synchronous=FULL (evidence must survive a power cut once committed).
    - Migrations are the numbered files in ./migrations, applied in order inside one BEGIN IMMEDIATE
      transaction each, recorded in schema_migrations with their sha256, and mirrored in PRAGMA user_version.
    - Before migrating a database that already holds data, an online backup is written beside it
      (<name>.pre-v<N>.bak). A failed migration rolls back and leaves the database at its previous version.
    - Refuses (SchemaError): a database newer than this code, an applied migration whose file changed,
      user_version disagreeing with schema_migrations, or a failed integrity check.
Connections use autocommit mode (isolation_level=None); callers open transactions explicitly.
"""

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


class SchemaError(RuntimeError):
    """The database cannot be used safely with this version of the code."""


DERIVED_MIGRATIONS_DIR = Path(__file__).with_name("derived_migrations")


def migrations(directory=None):
    """[(version, name, sql, sha256)] sorted by version. File names are NNNN_name.sql."""
    out = []
    for path in sorted(Path(directory or MIGRATIONS_DIR).glob("[0-9][0-9][0-9][0-9]_*.sql")):
        sql = path.read_text(encoding="utf-8")
        out.append((int(path.name[:4]), path.stem, sql, hashlib.sha256(sql.encode()).hexdigest()))
    versions = [m[0] for m in out]
    if versions != list(range(1, len(out) + 1)):
        raise SchemaError(f"migration files must be numbered 1..n without gaps, found {versions}")
    return out


LATEST_VERSION = len(migrations())


def statements(sql):
    """Split a migration into complete statements (trigger bodies contain ';', so a plain split is wrong)."""
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        if not buf and line.strip().startswith("--"):
            continue
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise SchemaError("migration ends with an incomplete statement")
    return out


def _private_file(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(fd, 0o600)
    os.close(fd)


def connect(path):
    path = Path(path)
    _private_file(path)
    connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA secure_delete=ON")      # deleted evidence (forget) is overwritten, not left in free pages
    return connection


def _applied(connection):
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not exists:
        return {}
    return {v: s for v, s in connection.execute("SELECT version, sha256 FROM schema_migrations")}


def _check(connection, known):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    applied = _applied(connection)
    if version > len(known):
        raise SchemaError(f"database is schema v{version}, this code knows up to v{len(known)}")
    if sorted(applied) != list(range(1, version + 1)):
        raise SchemaError(f"user_version {version} disagrees with recorded migrations {sorted(applied)}")
    for v, sha in applied.items():
        if known[v - 1][3] != sha:
            raise SchemaError(f"migration {v} on disk differs from the one applied to this database")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SchemaError("database failed its integrity check")
    return version


def _backup(connection, path, target_version):
    has_data = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    if not has_data:
        return None
    backup = Path(path).with_name(Path(path).name + f".pre-v{target_version}.bak")
    if backup.exists():
        raise SchemaError(f"a previous migration backup is still present: {backup}")
    _private_file(backup)
    copy = sqlite3.connect(backup)
    try:
        connection.backup(copy)
    finally:
        copy.close()
    return backup


def migrate(connection, path=None, known=None):
    """Apply every pending migration. Returns the list of versions applied."""
    known = known if known is not None else migrations()
    version = _check(connection, known)
    done = []
    for v, name, sql, sha in known[version:]:
        if path is not None:
            _backup(connection, path, v)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, "
                               "name TEXT NOT NULL, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)")
            for statement in statements(sql):
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                               (v, name, sha, datetime.now(timezone.utc).isoformat()))
            connection.execute(f"PRAGMA user_version={v}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        done.append(v)
    return done


def open_database(path, directory=None):
    connection = connect(path)
    try:
        migrate(connection, path, migrations(directory) if directory else None)
    except BaseException:
        connection.close()
        raise
    return connection
