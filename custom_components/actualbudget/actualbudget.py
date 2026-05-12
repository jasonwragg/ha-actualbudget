"""API to ActualBudget."""

from __future__ import annotations

import pathlib
import sqlite3
import types
from dataclasses import dataclass, field
from decimal import Decimal
import datetime
import logging
import threading
from typing import Any, Dict, List

from actual import Actual
from actual.exceptions import (
    ActualError,
    AuthorizationError,
    InvalidFile,
    InvalidZipFile,
    UnknownFileId,
)
from actual.database import reflect_model
from actual.migrations import js_migration_statements
from actual.queries import (
    get_accounts,
    get_accumulated_budgeted_balance,
    get_budgets,
)
from requests.exceptions import ConnectionError, SSLError


_LOGGER = logging.getLogger(__name__)

SESSION_TIMEOUT = datetime.timedelta(minutes=30)


def _normalize_cert(cert: Any) -> str | bool:
    """Return an ActualPy-compatible certificate verification setting.

    ActualPy expects ``True`` for default certificate verification, ``False``
    to skip verification, or a non-empty certificate string. Home Assistant
    optional text fields are commonly stored as ``None`` or an empty string,
    and passing those through to ActualPy makes it call
    ``ssl.SSLContext.load_verify_locations`` without any certificate data.
    """
    if cert is False:
        return False
    if cert is True:
        return True
    if isinstance(cert, str):
        cert = cert.strip()
        if cert.upper() == "SKIP":
            return False
        if cert:
            return cert
    return True


@dataclass
class BudgetMonth:
    month: str
    budgeted: float | None
    spent: float | None


@dataclass
class Budget:
    name: str
    months: List[BudgetMonth] = field(default_factory=list)
    accumulated_balance: Decimal = Decimal(0)


@dataclass
class Account:
    name: str | None
    balance: Decimal


@dataclass
class BudgetData:
    """Snapshot of all accounts and budgets at a point in time."""

    accounts: Dict[str, Account] = field(default_factory=dict)
    budgets: Dict[str, Budget] = field(default_factory=dict)


def _looks_like_html(content: bytes) -> bool:
    """Return whether server content appears to be an HTML fallback page."""
    return content.lstrip().lower().startswith((b"<", b"<!doctype html"))


def _data_file_with_relative_fallback(actual: Actual, file: str) -> bytes:
    """Fetch a migration file, preserving subpath deployments when needed."""
    migration = actual.data_file(file)
    if not _looks_like_html(migration):
        return migration

    response = actual._requests_session.get(f"data/{file}")
    response.raise_for_status()
    relative_migration = response.content
    if not _looks_like_html(relative_migration):
        return relative_migration

    return migration


def _run_migrations_safely(actual: Actual, migration_files: list[str]) -> None:
    """Run Actual migrations while ignoring HTML fallback responses.

    Some reverse proxies return the Actual web app HTML shell with HTTP 200 for
    missing ``/data/migrations/...`` files. Passing that HTML to SQLite raises
    ``near "<": syntax error``. Try a relative ``data/migrations/...`` path
    first for subpath deployments, then log and continue instead of crashing
    the integration if the server still returns HTML.
    """
    with sqlite3.connect(actual.data_dir / "db.sqlite") as conn:
        for file in migration_files:
            if not file.startswith("migrations/"):
                continue
            if not file.endswith((".sql", ".js")):
                _LOGGER.debug(
                    "Skipping non-SQL/JavaScript Actual Budget data file %s", file
                )
                continue

            migration_id = file.split("_")[0].split("/")[1]
            if conn.execute(
                "SELECT id FROM __migrations__ WHERE id = ?;", (migration_id,)
            ).fetchall():
                continue

            migration = _data_file_with_relative_fallback(actual, file)
            if _looks_like_html(migration):
                _LOGGER.warning(
                    "Skipping Actual Budget migration %s because the server "
                    "returned HTML instead of SQL/JavaScript. Check the "
                    "Actual Budget endpoint/reverse proxy /data path if "
                    "database features appear stale.",
                    file,
                )
                continue

            sql_statements = migration.decode()
            if file.endswith(".js"):
                sql_statements = "\n".join(js_migration_statements(sql_statements))

            conn.executescript(sql_statements)
            conn.execute("INSERT INTO __migrations__ (id) VALUES (?);", (migration_id,))
            conn.commit()

    if actual.engine is None:
        raise ActualError("Engine not initialized. Download or create a budget first.")
    actual._database_metadata = reflect_model(actual.engine)


class ActualBudget:
    """Interface to an Actual Budget server.

    All blocking operations must run in the executor via hass.async_add_executor_job.
    A reentrant lock serializes access to the Actual session so concurrent
    refreshes (e.g. poll + manual sync) don't corrupt SQLAlchemy state.
    """

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, hass, endpoint, password, file, cert, encrypt_password):
        self.hass = hass
        self.endpoint = endpoint
        self.password = password
        self.file = file
        self.cert = _normalize_cert(cert)
        self.encrypt_password = encrypt_password
        self.actual: Actual | None = None
        self.file_id = None
        self.session_started_at = datetime.datetime.now()
        self._lock = self._get_shared_lock(endpoint, file)

    @classmethod
    def _get_shared_lock(cls, endpoint: str, file: str) -> threading.RLock:
        """Return a process-wide lock for a specific endpoint+file combination."""
        lock_key = f"{endpoint}|{file}"
        with cls._locks_guard:
            lock = cls._locks.get(lock_key)
            if lock is None:
                lock = threading.RLock()
                cls._locks[lock_key] = lock
        return lock

    def _ensure_session(self):
        """Return a valid Actual session, creating one if needed.

        Caller must already hold self._lock.
        """
        now = datetime.datetime.now()
        if self.actual and self.session_started_at + SESSION_TIMEOUT < now:
            try:
                self.actual.__exit__(None, None, None)
            except Exception as err:
                _LOGGER.warning("Error closing stale Actual session: %s", err)
            self.actual = None

        if self.actual:
            try:
                result = self.actual.validate()
                if not result.data.validated:
                    raise RuntimeError("Session not validated")
            except Exception as err:
                _LOGGER.warning("Existing Actual session invalid, reconnecting: %s", err)
                self.actual = None

        if not self.actual:
            self.actual = self._create_session()
            self.session_started_at = now

        return self.actual.session

    def _create_session(self) -> Actual:
        actual = Actual(
            base_url=self.endpoint,
            password=self.password,
            cert=self.cert,
            encryption_password=self.encrypt_password,
            file=self.file,
        )
        self.file_id = str(actual._file.file_id)
        actual._data_dir = (
            pathlib.Path(self.hass.config.path("actualbudget")) / f"{self.file_id}"
        )
        _LOGGER.debug(f"Creating budget file on folder {actual._data_dir}")
        actual.run_migrations = types.MethodType(_run_migrations_safely, actual)
        actual.__enter__()
        result = actual.validate()
        if not result.data.validated:
            raise RuntimeError("Session not validated")
        return actual

    # -- bulk fetch ---------------------------------------------------------

    async def fetch_all(self) -> BudgetData:
        """Fetch all accounts and budgets in a single session lock acquisition."""
        return await self.hass.async_add_executor_job(self._fetch_all_sync)

    def _fetch_all_sync(self) -> BudgetData:
        with self._lock:
            session = self._ensure_session()
            today = datetime.date.today()

            data = BudgetData()

            for account in get_accounts(session):
                if account.name is None:
                    continue
                data.accounts[account.name] = Account(
                    name=account.name, balance=account.balance
                )

            budgets_by_name: Dict[str, Budget] = {}
            for raw in get_budgets(session):
                if not raw.category:
                    continue
                name = str(raw.category.name)
                if name not in budgets_by_name:
                    budgets_by_name[name] = Budget(name=name)
                budgeted = None if not raw.amount else float(raw.amount) / 100
                spent = float(raw.balance)
                budgets_by_name[name].months.append(
                    BudgetMonth(month=str(raw.month), budgeted=budgeted, spent=spent)
                )

            for name, budget in budgets_by_name.items():
                budget.months.sort(key=lambda m: m.month)
                try:
                    budget.accumulated_balance = get_accumulated_budgeted_balance(
                        session, today, name
                    )
                except (AttributeError, TypeError):
                    budget.accumulated_balance = Decimal(0)

            data.budgets = budgets_by_name
            return data

    # -- sync actions -------------------------------------------------------

    async def run_bank_sync(self) -> None:
        """Trigger a bank sync on the Actual server and commit."""
        await self.hass.async_add_executor_job(self._run_bank_sync)

    def _run_bank_sync(self) -> None:
        with self._lock:
            self._ensure_session()
            self.actual.sync()
            self.actual.run_bank_sync()
            self.actual.commit()

    async def run_budget_sync(self) -> None:
        """Pull latest budget file from the server."""
        await self.hass.async_add_executor_job(self._run_budget_sync)

    def _run_budget_sync(self) -> None:
        with self._lock:
            self._ensure_session()
            self.actual.sync()

    async def async_close(self) -> None:
        """Close any active Actual session."""
        await self.hass.async_add_executor_job(self._close_session)

    def _close_session(self) -> None:
        with self._lock:
            if not self.actual:
                return
            try:
                self.actual.__exit__(None, None, None)
            except Exception as err:
                _LOGGER.warning("Error closing Actual session: %s", err)
            finally:
                self.actual = None

    # -- connection test ----------------------------------------------------

    async def test_connection(self):
        return await self.hass.async_add_executor_job(self._test_connection_sync)

    def _test_connection_sync(self):
        try:
            with self._lock:
                session = self._ensure_session()
                if not session:
                    return "failed_file"
        except (SSLError, TypeError, ValueError):
            return "failed_ssl"
        except ConnectionError:
            return "failed_connection"
        except AuthorizationError:
            return "failed_auth"
        except (UnknownFileId, InvalidFile, InvalidZipFile):
            return "failed_file"
        except sqlite3.OperationalError as err:
            _LOGGER.warning("Actual Budget local database migration failed: %s", err)
            return "failed_migration"
        except Exception:
            _LOGGER.exception("Unexpected error testing Actual Budget connection")
            return "failed_unknown"
        return None
