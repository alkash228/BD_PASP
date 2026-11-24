from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pyodbc

from pasp.sqlfmt import (
    ColumnMeta,
    create_table_sql,
    format_type,
    quote_ident,
    script_header,
    split_batches,
    write_insert_batch,
)

_PREFERRED_DRIVERS = (
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "ODBC Driver 13 for SQL Server",
    "SQL Server",
)
_SKIP_TYPES = {"timestamp", "rowversion"}
_INSERT_BATCH = 200


class ExportCancelled(Exception):
    pass


@dataclass(frozen=True)
class ConnParams:
    server: str
    database: str
    auth: str
    username: str
    password: str
    trust_cert: bool = True


def installed_sql_drivers() -> list[str]:
    return [name for name in pyodbc.drivers() if "SQL Server" in name]


def pick_driver() -> str:
    installed = set(pyodbc.drivers())
    for name in _PREFERRED_DRIVERS:
        if name in installed:
            return name
    found = ", ".join(installed_sql_drivers()) or "нет"
    raise RuntimeError(
        "Не найден драйвер ODBC для SQL Server. "
        "Установите Microsoft ODBC Driver 18 for SQL Server. "
        f"Сейчас в системе: {found}."
    )


def odbc_value(value: str) -> str:
    if any(char in value for char in ";{}="):
        return "{" + value.replace("}", "}}") + "}"
    return value


def connection_string(params: ConnParams, driver: str | None = None) -> str:
    chosen = driver or pick_driver()
    parts = [
        f"DRIVER={{{chosen}}}",
        f"SERVER={odbc_value(params.server)}",
        "APP=BD PASP",
    ]
    # Старый драйвер «SQL Server» не понимает TrustServerCertificate.
    if chosen.startswith("ODBC Driver"):
        parts.insert(2, "Encrypt=yes")
        parts.insert(3, f"TrustServerCertificate={'yes' if params.trust_cert else 'no'}")
    if params.database:
        parts.append(f"DATABASE={odbc_value(params.database)}")
    if params.auth == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={odbc_value(params.username)}")
        parts.append(f"PWD={odbc_value(params.password)}")
    return ";".join(parts) + ";"


def connect(params: ConnParams, *, login_timeout: int = 15, query_timeout: int = 0):
    connection = pyodbc.connect(
        connection_string(params),
        autocommit=True,
        timeout=login_timeout,
    )
    connection.timeout = query_timeout
    return connection


def format_db_error(exc: BaseException) -> str:
    if isinstance(exc, pyodbc.Error):
        parts = [str(part) for part in exc.args if part]
        if parts:
            return "\n".join(parts)
    return str(exc)


def current_database(connection) -> str:
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT DB_NAME()")
        row = cursor.fetchone()
        return "" if row is None or row[0] is None else str(row[0])
    finally:
        cursor.close()


_SYSTEM_DATABASES = {"master", "model", "msdb", "tempdb"}


def list_databases(connection) -> list[dict]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT name, state
            FROM sys.databases
            ORDER BY name
            """
        )
        entries: list[dict] = []
        for row in cursor.fetchall():
            name = str(row[0])
            entries.append(
                {
                    "name": name,
                    "online": int(row[1]) == 0,
                    "system": name.lower() in _SYSTEM_DATABASES,
                }
            )
        return entries
    finally:
        cursor.close()


def _named_rows(cursor, sql: str) -> list[dict]:
    cursor.execute(sql)
    items: list[dict] = []
    for row in cursor.fetchall():
        items.append(
            {
                "schema": str(row[0]),
                "name": str(row[1]),
                "system": bool(row[2]),
            }
        )
    return items


def database_catalog(connection) -> dict:
    """Имена объектов одной базы. Без столбцов и без строк данных."""
    cursor = connection.cursor()
    try:
        tables = _named_rows(
            cursor,
            """
            SELECT s.name, t.name, t.is_ms_shipped
            FROM sys.tables AS t
            INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            ORDER BY t.is_ms_shipped, s.name, t.name
            """,
        )
        views = _named_rows(
            cursor,
            """
            SELECT s.name, v.name, v.is_ms_shipped
            FROM sys.views AS v
            INNER JOIN sys.schemas AS s ON s.schema_id = v.schema_id
            ORDER BY v.is_ms_shipped, s.name, v.name
            """,
        )
        procedures = _named_rows(
            cursor,
            """
            SELECT s.name, p.name, p.is_ms_shipped
            FROM sys.procedures AS p
            INNER JOIN sys.schemas AS s ON s.schema_id = p.schema_id
            WHERE p.type IN ('P', 'PC')
            ORDER BY p.is_ms_shipped, s.name, p.name
            """,
        )
        cursor.execute(
            """
            SELECT s.name, o.name, o.type, o.is_ms_shipped
            FROM sys.objects AS o
            INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
            WHERE o.type IN ('FN', 'IF', 'TF', 'FS', 'FT', 'AF')
            ORDER BY o.is_ms_shipped, s.name, o.name
            """
        )
        functions: list[dict] = []
        for row in cursor.fetchall():
            code = str(row[2]).strip().upper()
            if code in {"IF", "TF", "FT"}:
                group = "table"
            elif code == "AF":
                group = "aggregate"
            else:
                group = "scalar"
            functions.append(
                {
                    "schema": str(row[0]),
                    "name": str(row[1]),
                    "group": group,
                    "system": bool(row[3]),
                }
            )
        cursor.execute(
            """
            SELECT s.name, syn.name
            FROM sys.synonyms AS syn
            INNER JOIN sys.schemas AS s ON s.schema_id = syn.schema_id
            ORDER BY s.name, syn.name
            """
        )
        synonyms = [{"schema": str(row[0]), "name": str(row[1])} for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT name
            FROM sys.triggers
            WHERE parent_class = 0
            ORDER BY name
            """
        )
        triggers = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT s.name, t.name, t.is_table_type
            FROM sys.types AS t
            INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            WHERE t.is_user_defined = 1
            ORDER BY t.is_table_type, s.name, t.name
            """
        )
        types = [
            {"schema": str(row[0]), "name": str(row[1]), "table_type": bool(row[2])}
            for row in cursor.fetchall()
        ]
        return {
            "tables": tables,
            "views": views,
            "procedures": procedures,
            "functions": functions,
            "synonyms": synonyms,
            "triggers": triggers,
            "types": types,
        }
    finally:
        cursor.close()


def _find_object_id(cursor, schema: str, name: str) -> int | None:
    cursor.execute(
        """
        SELECT o.object_id
        FROM sys.objects AS o
        INNER JOIN sys.schemas AS s ON s.schema_id = o.schema_id
        WHERE s.name = ? AND o.name = ?
        """,
        schema,
        name,
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return int(row[0])


def object_details(connection, schema: str, name: str, kind: str) -> dict:
    cursor = connection.cursor()
    try:
        object_id = _find_object_id(cursor, schema, name)
        if object_id is None:
            return {"columns": [], "parameters": [], "keys": [], "checks": [], "triggers": [], "indexes": [], "stats": []}
        cursor.execute(
            """
            SELECT c.name, ty.name, c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity
            FROM sys.columns AS c
            INNER JOIN sys.types AS ty ON c.user_type_id = ty.user_type_id
            WHERE c.object_id = ?
            ORDER BY c.column_id
            """,
            object_id,
        )
        columns = []
        for row in cursor.fetchall():
            type_sql = format_type(str(row[1]), int(row[2]), int(row[3]), int(row[4]))
            notes = [type_sql, "NULL" if row[5] else "NOT NULL"]
            if row[6]:
                notes.append("IDENTITY")
            columns.append(f"{row[0]} ({', '.join(notes)})")
        cursor.execute(
            """
            SELECT p.name, ty.name, p.max_length, p.precision, p.scale, p.is_output
            FROM sys.parameters AS p
            INNER JOIN sys.types AS ty ON p.user_type_id = ty.user_type_id
            WHERE p.object_id = ? AND p.parameter_id > 0
            ORDER BY p.parameter_id
            """,
            object_id,
        )
        parameters = []
        for row in cursor.fetchall():
            type_sql = format_type(str(row[1]), int(row[2]), int(row[3]), int(row[4]))
            suffix = " OUTPUT" if row[5] else ""
            label = row[0] or "возвращаемое"
            parameters.append(f"{label} ({type_sql}){suffix}")
        if kind != "table":
            return {
                "columns": columns,
                "parameters": parameters,
                "keys": [],
                "checks": [],
                "triggers": [],
                "indexes": [],
                "stats": [],
            }
        cursor.execute(
            """
            SELECT name, type_desc
            FROM sys.key_constraints
            WHERE parent_object_id = ?
            ORDER BY name
            """,
            object_id,
        )
        keys = [f"{row[0]} ({row[1]})" for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT name
            FROM sys.foreign_keys
            WHERE parent_object_id = ?
            ORDER BY name
            """,
            object_id,
        )
        keys.extend(f"{row[0]} (FOREIGN KEY)" for row in cursor.fetchall())
        cursor.execute(
            """
            SELECT name FROM sys.check_constraints WHERE parent_object_id = ? ORDER BY name
            """,
            object_id,
        )
        checks = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT name FROM sys.default_constraints WHERE parent_object_id = ? ORDER BY name
            """,
            object_id,
        )
        checks.extend(str(row[0]) for row in cursor.fetchall())
        cursor.execute(
            """
            SELECT name FROM sys.triggers WHERE parent_id = ? ORDER BY name
            """,
            object_id,
        )
        triggers = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT name
            FROM sys.indexes
            WHERE object_id = ? AND index_id > 0 AND name IS NOT NULL
            ORDER BY name
            """,
            object_id,
        )
        indexes = [str(row[0]) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT name FROM sys.stats WHERE object_id = ? AND name IS NOT NULL ORDER BY name
            """,
            object_id,
        )
        stats = [str(row[0]) for row in cursor.fetchall()]
        return {
            "columns": columns,
            "parameters": parameters,
            "keys": keys,
            "checks": checks,
            "triggers": triggers,
            "indexes": indexes,
            "stats": stats,
        }
    finally:
        cursor.close()


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def column_meta(connection, schema: str, table: str) -> list[ColumnMeta]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                c.name,
                ty.name,
                c.max_length,
                c.precision,
                c.scale,
                c.is_nullable,
                c.is_identity,
                c.is_computed,
                ic.seed_value,
                ic.increment_value
            FROM sys.columns AS c
            INNER JOIN sys.types AS ty ON c.user_type_id = ty.user_type_id
            INNER JOIN sys.tables AS t ON t.object_id = c.object_id
            INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            LEFT JOIN sys.identity_columns AS ic
                ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            WHERE s.name = ? AND t.name = ?
            ORDER BY c.column_id
            """,
            schema,
            table,
        )
        columns: list[ColumnMeta] = []
        for row in cursor.fetchall():
            type_name = str(row[1])
            columns.append(
                ColumnMeta(
                    name=str(row[0]),
                    type_sql=format_type(type_name, int(row[2]), int(row[3]), int(row[4])),
                    nullable=bool(row[5]),
                    identity=bool(row[6]),
                    seed=_as_int(row[8]),
                    increment=_as_int(row[9]),
                    computed=bool(row[7]),
                    type_name=type_name,
                )
            )
        return columns
    finally:
        cursor.close()


def primary_key(connection, schema: str, table: str) -> list[str]:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT col.name
            FROM sys.indexes AS i
            INNER JOIN sys.index_columns AS ic
                ON ic.object_id = i.object_id AND ic.index_id = i.index_id
            INNER JOIN sys.columns AS col
                ON col.object_id = ic.object_id AND col.column_id = ic.column_id
            INNER JOIN sys.tables AS t ON t.object_id = i.object_id
            INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            WHERE i.is_primary_key = 1
              AND ic.is_included_column = 0
              AND s.name = ? AND t.name = ?
            ORDER BY ic.key_ordinal
            """,
            schema,
            table,
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        cursor.close()


def approximate_rows(connection, schema: str, table: str) -> int:
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT COALESCE(SUM(p.[rows]), 0)
            FROM sys.partitions AS p
            INNER JOIN sys.tables AS t ON t.object_id = p.object_id
            INNER JOIN sys.schemas AS s ON s.schema_id = t.schema_id
            WHERE s.name = ? AND t.name = ? AND p.index_id IN (0, 1)
            """,
            schema,
            table,
        )
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])
    finally:
        cursor.close()


def export_table(
    params: ConnParams,
    schema: str,
    table: str,
    destination: Path,
    *,
    include_create: bool,
    stop: threading.Event,
    on_progress,
) -> int:
    """Потоковая выгрузка. Файл заменяет прежний только после успешной записи."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    connection = connect(params, query_timeout=0)
    written = 0
    try:
        columns = column_meta(connection, schema, table)
        insert_columns = [
            column
            for column in columns
            if not column.computed and column.type_name.lower() not in _SKIP_TYPES
        ]
        if not insert_columns:
            raise RuntimeError(f"У {schema}.{table} нет столбцов, которые можно перезалить.")
        total = approximate_rows(connection, schema, table)
        keys = primary_key(connection, schema, table)
        names = [column.name for column in insert_columns]
        key_names = [name for name in keys if name in names]
        order = ""
        if key_names:
            order = " ORDER BY " + ", ".join(quote_ident(name) for name in key_names)
        select_list = ", ".join(quote_ident(name) for name in names)
        statement = (
            f"SELECT {select_list} FROM {quote_ident(schema)}.{quote_ident(table)}{order}"
        )
        identity = any(column.identity for column in insert_columns)
        cursor = connection.cursor()
        cursor.arraysize = _INSERT_BATCH
        try:
            cursor.execute(statement)
            with partial.open("w", encoding="utf-8-sig", newline="\n") as handle:
                handle.write(script_header())
                if include_create:
                    handle.write(create_table_sql(schema, table, columns))
                while True:
                    if stop.is_set():
                        raise ExportCancelled()
                    rows = cursor.fetchmany(_INSERT_BATCH)
                    if not rows:
                        break
                    write_insert_batch(
                        handle,
                        schema,
                        table,
                        names,
                        [tuple(row) for row in rows],
                        identity=identity,
                    )
                    written += len(rows)
                    on_progress(written, total)
        finally:
            cursor.close()
        partial.replace(destination)
        return written
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        connection.close()


def export_query(
    params: ConnParams,
    sql: str,
    schema: str,
    table: str,
    destination: Path,
    *,
    identity: bool,
    stop: threading.Event,
    on_progress,
) -> int:
    """Выполняет запрос и пишет результат в .sql пакетами INSERT, не держа всю выборку в памяти."""
    batches = split_batches(sql)
    if not batches:
        raise RuntimeError("Пустой запрос.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    connection = connect(params, query_timeout=0)
    written = 0
    try:
        cursor = connection.cursor()
        cursor.arraysize = _INSERT_BATCH
        try:
            with partial.open("w", encoding="utf-8-sig", newline="\n") as handle:
                handle.write(script_header())
                for batch in batches:
                    if stop.is_set():
                        raise ExportCancelled()
                    cursor.execute(batch)
                    while True:
                        if cursor.description:
                            names = [item[0] or f"Column{index + 1}" for index, item in enumerate(cursor.description)]
                            while True:
                                if stop.is_set():
                                    raise ExportCancelled()
                                rows = cursor.fetchmany(_INSERT_BATCH)
                                if not rows:
                                    break
                                write_insert_batch(
                                    handle,
                                    schema,
                                    table,
                                    names,
                                    [tuple(row) for row in rows],
                                    identity=identity,
                                )
                                written += len(rows)
                                on_progress(written, 0)
                        if not cursor.nextset():
                            break
                if written == 0:
                    handle.write("-- запрос не вернул строк\n")
        finally:
            cursor.close()
        partial.replace(destination)
        return written
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
