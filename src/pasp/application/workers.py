from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from pasp.infrastructure.mssql import (
    ConnParams,
    ExportCancelled,
    column_meta,
    connect,
    current_database,
    database_catalog,
    export_query,
    export_table,
    format_db_error,
    list_databases,
    object_details,
)
from pasp.sqlfmt import (
    format_cell,
    insert_column_names,
    merge_staged_rows,
    retarget_export_script,
    split_batches,
    staging_table_sql,
)

_FETCH_BATCH = 2000


def _sql_statements(sql: str) -> list[str]:
    parts = []
    for chunk in sql.split(";\n"):
        text = chunk.strip()
        if not text:
            continue
        if not text.endswith(";"):
            text += ";"
        parts.append(text)
    return parts


def _cancelled(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "cancel" in text or "отмен" in text


class ConnectWorker(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, params: ConnParams):
        super().__init__()
        self._params = params

    def run(self) -> None:
        connection = None
        try:
            connection = connect(self._params, login_timeout=15, query_timeout=15)
            name = current_database(connection)
            self.succeeded.emit(name)
        except Exception as exc:
            self.failed.emit(format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()


class MetaWorker(QThread):
    ready = pyqtSignal(str, list)
    failed = pyqtSignal(str)

    def __init__(self, params: ConnParams):
        super().__init__()
        self._params = params

    def run(self) -> None:
        connection = None
        try:
            connection = connect(self._params, login_timeout=15, query_timeout=30)
            database = current_database(connection)
            databases = list_databases(connection)
            self.ready.emit(database, databases)
        except Exception as exc:
            self.failed.emit(format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()


class CatalogWorker(QThread):
    ready = pyqtSignal(str, dict)
    failed = pyqtSignal(str, str)

    def __init__(self, params: ConnParams, database: str):
        super().__init__()
        self._params = params
        self._database = database

    def run(self) -> None:
        connection = None
        try:
            connection = connect(replace(self._params, database=self._database), login_timeout=15, query_timeout=60)
            self.ready.emit(self._database, database_catalog(connection))
        except Exception as exc:
            self.failed.emit(self._database, format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()


class DetailWorker(QThread):
    ready = pyqtSignal(str, str, str, str, dict)
    failed = pyqtSignal(str, str, str, str, str)

    def __init__(self, params: ConnParams, database: str, schema: str, name: str, kind: str):
        super().__init__()
        self._params = params
        self._database = database
        self._schema = schema
        self._name = name
        self._kind = kind

    def run(self) -> None:
        connection = None
        try:
            connection = connect(replace(self._params, database=self._database), login_timeout=15, query_timeout=30)
            details = object_details(connection, self._schema, self._name, self._kind)
            self.ready.emit(self._database, self._schema, self._name, self._kind, details)
        except Exception as exc:
            self.failed.emit(self._database, self._schema, self._name, self._kind, format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()


class QueryWorker(QThread):
    """Читает весь результат пачками. Следующая пачка с сервера читается,
    пока интерфейс вставляет предыдущую, и не копится больше одной.
    """

    columns_ready = pyqtSignal(list)
    batch_ready = pyqtSignal(list)
    message = pyqtSignal(str)
    succeeded = pyqtSignal(int, bool)
    failed = pyqtSignal(str)

    def __init__(self, params: ConnParams, sql: str):
        super().__init__()
        self._params = params
        self._sql = sql
        self._stop = threading.Event()
        self._ack = threading.Event()
        self.spid: int | None = None

    def ack(self) -> None:
        self._ack.set()

    def stop(self) -> None:
        self._stop.set()
        self._ack.set()

    def _wait_ack(self) -> bool:
        while not self._ack.wait(0.25):
            if self._stop.is_set():
                return False
        return not self._stop.is_set()

    def run(self) -> None:
        connection = None
        last_rows = 0
        truncated = False
        try:
            connection = connect(self._params, query_timeout=0)
            cursor = connection.cursor()
            cursor.arraysize = _FETCH_BATCH
            try:
                cursor.execute("SELECT @@SPID")
                spid_row = cursor.fetchone()
                self.spid = int(spid_row[0]) if spid_row else None
                batches = split_batches(self._sql)
                if not batches:
                    self.message.emit("Пустой запрос.")
                    self.succeeded.emit(0, False)
                    return
                for index, batch in enumerate(batches, start=1):
                    if self._stop.is_set():
                        self.message.emit("Запрос отменён.")
                        break
                    if len(batches) > 1:
                        self.message.emit(f"Пакет {index} из {len(batches)}")
                    cursor.execute(batch)
                    last_rows, truncated, stopped = self._drain(cursor)
                    if stopped or truncated:
                        break
                if self._stop.is_set():
                    self.message.emit("Запрос отменён.")
                self.succeeded.emit(last_rows, truncated)
            finally:
                cursor.close()
        except Exception as exc:
            if self._stop.is_set() or _cancelled(exc):
                self.message.emit("Запрос отменён.")
                self.succeeded.emit(last_rows, truncated)
            else:
                self.failed.emit(format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()

    def _drain(self, cursor) -> tuple[int, bool, bool]:
        last_rows = 0
        noted = False
        while True:
            if self._stop.is_set():
                return last_rows, False, True
            if cursor.description:
                noted = True
                columns = [item[0] or "" for item in cursor.description]
                self.columns_ready.emit(columns)
                set_rows = 0
                rows = cursor.fetchmany(_FETCH_BATCH)
                while rows:
                    if self._stop.is_set():
                        return set_rows, False, True
                    display = [[format_cell(value) for value in row] for row in rows]
                    set_rows += len(display)
                    self._ack.clear()
                    self.batch_ready.emit(display)
                    rows = cursor.fetchmany(_FETCH_BATCH)
                    if not self._wait_ack():
                        return set_rows, False, True
                if set_rows == 0:
                    self.message.emit("Запрос не вернул строк.")
                else:
                    self.message.emit(f"Строк: {set_rows}.")
                last_rows = set_rows
            elif cursor.rowcount >= 0:
                noted = True
                self.message.emit(f"Затронуто строк: {cursor.rowcount}.")
            if not cursor.nextset():
                if not noted:
                    self.message.emit("Команда выполнена.")
                return last_rows, False, False


class KillWorker(QThread):
    failed = pyqtSignal(str)

    def __init__(self, params: ConnParams, spid: int):
        super().__init__()
        self._params = params
        self._spid = int(spid)

    def run(self) -> None:
        connection = None
        try:
            connection = connect(self._params, login_timeout=5, query_timeout=5)
            cursor = connection.cursor()
            try:
                cursor.execute(f"KILL {self._spid}")
            finally:
                cursor.close()
        except Exception as exc:
            self.failed.emit(format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()


class ExportWorker(QThread):
    progress = pyqtSignal(str, int, int)
    table_finished = pyqtSignal(str, int, str)
    table_failed = pyqtSignal(str, str)
    finished_all = pyqtSignal(int, int)

    def __init__(self, params: ConnParams, jobs: list[tuple[str, str, Path]], include_create: bool):
        super().__init__()
        self._params = params
        self._jobs = jobs
        self._include_create = include_create
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if not self._jobs:
            self.finished_all.emit(0, 0)
            return
        ok = 0
        bad = 0
        workers = min(2, len(self._jobs))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pasp-export") as pool:
            futures = {pool.submit(self._one, job): job for job in self._jobs}
            for future in as_completed(futures):
                database, schema, table, path = futures[future]
                key = f"{database}.{schema}.{table}"
                try:
                    count = future.result()
                    ok += 1
                    self.table_finished.emit(key, count, str(path))
                except ExportCancelled:
                    bad += 1
                    self.table_failed.emit(key, "Отменено")
                except Exception as exc:
                    bad += 1
                    self.table_failed.emit(key, format_db_error(exc))
        self.finished_all.emit(ok, bad)

    def _one(self, job: tuple[str, str, str, Path]) -> int:
        database, schema, table, path = job
        key = f"{database}.{schema}.{table}"
        params = replace(self._params, database=database)
        last_emit = 0.0

        def on_progress(written: int, total: int) -> None:
            nonlocal last_emit
            now = time.monotonic()
            if written != total and now - last_emit < 0.2:
                return
            last_emit = now
            self.progress.emit(key, written, total)

        return export_table(
            params,
            schema,
            table,
            path,
            include_create=self._include_create,
            stop=self._stop,
            on_progress=on_progress,
        )


class QueryExportWorker(QThread):
    """Пишет результат запроса в заранее указанную папку и не трогает окно."""

    succeeded = pyqtSignal(str, int)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int)

    def __init__(self, params: ConnParams, sql: str, schema: str, table: str, destination: Path, identity: bool):
        super().__init__()
        self._params = params
        self._sql = sql
        self._schema = schema
        self._table = table
        self._destination = destination
        self._identity = identity
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            count = export_query(
                self._params,
                self._sql,
                self._schema,
                self._table,
                self._destination,
                identity=self._identity,
                stop=self._stop,
                on_progress=lambda written, _total: self.progress.emit(written),
            )
            self.succeeded.emit(str(self._destination), count)
        except ExportCancelled:
            self.failed.emit("Отменено")
        except Exception as exc:
            self.failed.emit(format_db_error(exc))


class ScriptWorker(QThread):
    """Обновляет строки по первой колонке файла и добавляет новые. Старые не удаляет."""

    message = pyqtSignal(str)
    progress = pyqtSignal(int, int)
    succeeded = pyqtSignal(int)
    failed = pyqtSignal(str)

    def __init__(self, params: ConnParams, path: Path, schema: str, table: str):
        super().__init__()
        self._params = params
        self._path = path
        self._schema = schema
        self._table = table
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        connection = None
        try:
            sql = retarget_export_script(self._path.read_text(encoding="utf-8-sig"), self._schema, self._table)
            columns = insert_column_names(sql)
            batches = split_batches(sql)
            if not columns or not batches:
                self.failed.emit("В файле нет строк выгруженной таблицы.")
                return
            connection = connect(self._params, query_timeout=0)
            meta = column_meta(connection, self._schema, self._table)
            if not meta:
                self.failed.emit(f"Таблицы [{self._schema}].[{self._table}] нет на этом сервере.")
                return
            known = {column.name: column for column in meta}
            missing = [name for name in columns if name not in known]
            if missing:
                self.failed.emit("В таблице на сервере нет колонок из файла: " + ", ".join(missing))
                return
            identity = [column.name for column in meta if column.identity]
            keys = [columns[0]]
            cursor = connection.cursor()
            try:
                connection.autocommit = False
                cursor.execute(staging_table_sql([(name, known[name].type_sql) for name in columns]))
                total = len(batches) + 1
                for index, batch in enumerate(batches, start=1):
                    if self._stop.is_set():
                        connection.rollback()
                        self.failed.emit("Отменено")
                        return
                    self.progress.emit(index, total)
                    cursor.execute(batch)
                    self._discard(cursor)
                if self._stop.is_set():
                    connection.rollback()
                    self.failed.emit("Отменено")
                    return
                self.progress.emit(total, total)
                for statement in _sql_statements(merge_staged_rows(self._schema, self._table, columns, keys, identity)):
                    cursor.execute(statement)
                    self._discard(cursor)
                connection.commit()
                self.succeeded.emit(total)
            finally:
                cursor.close()
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            if self._stop.is_set() or _cancelled(exc):
                self.failed.emit("Отменено")
            else:
                self.failed.emit(format_db_error(exc))
        finally:
            if connection is not None:
                connection.close()

    def _discard(self, cursor) -> None:
        while True:
            if self._stop.is_set():
                return
            if cursor.description:
                while cursor.fetchmany(2000):
                    if self._stop.is_set():
                        return
            if not cursor.nextset():
                return
