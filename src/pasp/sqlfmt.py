"""Литералы и пакеты T-SQL для перезаливки в SQL Server.

Скрипт пишется на диск построчно: в памяти остаётся только текущее значение,
а не вся таблица.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Iterable, TextIO

_GO = re.compile(r"^GO(?:\s+(\d+))?(?:\s+--.*)?$", re.IGNORECASE)
_MAX_GO_REPEAT = 100


@dataclass(frozen=True)
class ColumnMeta:
    name: str
    type_sql: str
    nullable: bool
    identity: bool
    seed: int | None
    increment: int | None
    computed: bool
    type_name: str


def quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NULL"
        return repr(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return "'" + value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "'"
    if isinstance(value, date):
        return "'" + value.isoformat() + "'"
    if isinstance(value, time):
        return "'" + value.isoformat() + "'"
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, uuid.UUID):
        return "'" + str(value) + "'"
    text = value if isinstance(value, str) else str(value)
    return "N'" + text.replace("'", "''") + "'"


def format_cell(value: object) -> str:
    """Короткое представление для сетки. Длинный binary в модель не кладём."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        if len(raw) > 32:
            return f"0x{raw[:32].hex()}… ({len(raw)} байт)"
        return "0x" + raw.hex()
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return ""
        return repr(value)
    return str(value)


def format_type(type_name: str, max_length: int, precision: int, scale: int) -> str:
    name = (type_name or "nvarchar").lower()
    if name in {"varchar", "char", "varbinary", "binary"}:
        if max_length < 0:
            return f"{name}(max)"
        return f"{name}({max_length})"
    if name in {"nvarchar", "nchar"}:
        if max_length < 0:
            return f"{name}(max)"
        return f"{name}({max(1, max_length // 2)})"
    if name in {"decimal", "numeric"}:
        return f"{name}({precision},{scale})"
    if name in {"datetime2", "datetimeoffset", "time"}:
        return f"{name}({scale})"
    if name == "float":
        return "float" if precision in (0, 53) else f"float({precision})"
    return name


def split_batches(sql: str) -> list[str]:
    """Разбить скрипт по строке GO, как это делает SSMS. Повтор GO N ограничен."""
    batches: list[str] = []
    buf: list[str] = []

    def flush(times: int) -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return
        repeats = max(1, min(times, _MAX_GO_REPEAT))
        batches.extend([text] * repeats)

    for line in sql.splitlines():
        match = _GO.match(line.strip())
        if match:
            flush(int(match.group(1) or "1"))
        else:
            buf.append(line)
    tail = "\n".join(buf).strip()
    if tail:
        batches.append(tail)
    return batches


def script_header() -> str:
    return (
        "/* BD PASP — выгрузка для перезаливки в SQL Server */\n"
        "SET NOCOUNT ON;\n"
        "SET XACT_ABORT ON;\n"
        "SET ANSI_NULLS ON;\n"
        "SET QUOTED_IDENTIFIER ON;\n"
        "GO\n"
    )


def create_table_sql(schema: str, table: str, columns: Iterable[ColumnMeta]) -> str:
    usable = [
        column
        for column in columns
        if not column.computed and column.type_name.lower() not in {"timestamp", "rowversion"}
    ]
    lines: list[str] = []
    for column in usable:
        identity = ""
        if column.identity:
            seed = 1 if column.seed is None else column.seed
            increment = 1 if column.increment is None else column.increment
            identity = f" IDENTITY({seed},{increment})"
        nulls = "NULL" if column.nullable else "NOT NULL"
        lines.append(f"    {quote_ident(column.name)} {column.type_sql}{identity} {nulls}")
    body = ",\n".join(lines) if lines else "    [placeholder] int NULL"
    qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
    object_name = (schema + "." + table).replace("'", "''")
    return (
        f"IF OBJECT_ID(N'{object_name}', N'U') IS NULL\n"
        "BEGIN\n"
        f"CREATE TABLE {qualified} (\n{body}\n);\n"
        "END\n"
        "GO\n"
    )


def write_insert_batch(
    handle: TextIO,
    schema: str,
    table: str,
    column_names: list[str],
    rows: list[tuple],
    *,
    identity: bool,
) -> None:
    qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
    cols = ", ".join(quote_ident(name) for name in column_names)
    if identity:
        handle.write(f"SET IDENTITY_INSERT {qualified} ON;\n")
    handle.write("BEGIN TRANSACTION;\n")
    handle.write(f"INSERT INTO {qualified} ({cols}) VALUES\n")
    for index, row in enumerate(rows):
        if index:
            handle.write(",\n")
        handle.write("(")
        for cell_index, value in enumerate(row):
            if cell_index:
                handle.write(", ")
            handle.write(sql_literal(value))
        handle.write(")")
    handle.write(";\nCOMMIT TRANSACTION;\n")
    if identity:
        handle.write(f"SET IDENTITY_INSERT {qualified} OFF;\n")
    handle.write("GO\n")


def safe_filename(schema: str, table: str) -> str:
    raw = f"{schema}.{table}.sql"
    return "".join("_" if char in '<>:"/\\|?*' else char for char in raw)


_TARGET = re.compile(
    r"INSERT\s+INTO\s+\[((?:\]\]|[^\]])*)\]\.\[((?:\]\]|[^\]])*)\]",
    re.IGNORECASE,
)
_BATCH_TX = re.compile(r"^(?:BEGIN|COMMIT) TRANSACTION;\s*$", re.IGNORECASE | re.MULTILINE)
_QUALIFIED = r"\[(?:\]\]|[^\]])*\]\.\[(?:\]\]|[^\]])*\]"
_IDENTITY_LINE = re.compile(
    rf"^SET\s+IDENTITY_INSERT\s+{_QUALIFIED}\s+(?:ON|OFF);\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_INSERT_COLUMNS = re.compile(
    r"INSERT\s+INTO\s+#pasp_load\s*\(([^)]*)\)\s*VALUES",
    re.IGNORECASE,
)
_BRACKET = re.compile(r"\[((?:\]\]|[^\]])*)\]")


def script_target(sql: str) -> tuple[str, str] | None:
    """Схема и таблица из файла выгрузки, который пишет сама программа."""
    match = _TARGET.search(sql)
    if match is None:
        return None
    return match.group(1).replace("]]", "]"), match.group(2).replace("]]", "]")


def retarget_export_script(sql: str, schema: str, table: str) -> str:
    """Кладёт строки файла во временную таблицу. Строки на сервере не удаляет."""
    found = script_target(sql)
    if found is None:
        raise ValueError("В файле нет выгрузки таблицы: не найден INSERT INTO [схема].[таблица].")
    old_schema, old_table = found
    old_qualified = f"{quote_ident(old_schema)}.{quote_ident(old_table)}"
    new_qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
    if old_qualified != new_qualified:
        sql = sql.replace(old_qualified, new_qualified)
    old_object = (old_schema + "." + old_table).replace("'", "''")
    new_object = (schema + "." + table).replace("'", "''")
    old_id = f"OBJECT_ID(N'{old_object}'"
    new_id = f"OBJECT_ID(N'{new_object}'"
    if old_id != new_id:
        sql = sql.replace(old_id, new_id)
    sql = _BATCH_TX.sub("", sql)
    sql = _IDENTITY_LINE.sub("", sql)
    sql = sql.replace(f"INSERT INTO {new_qualified}", "INSERT INTO #pasp_load")
    if "INSERT INTO #pasp_load" not in sql:
        raise ValueError("В файле нет INSERT выгрузки таблицы.")
    return sql


def insert_column_names(sql: str) -> list[str]:
    match = _INSERT_COLUMNS.search(sql)
    if match is None:
        return []
    return [part.replace("]]", "]") for part in _BRACKET.findall(match.group(1))]


def staging_table_sql(columns: list[tuple[str, str]]) -> str:
    body = ",\n".join(f"    {quote_ident(name)} {type_sql} NULL" for name, type_sql in columns)
    return f"CREATE TABLE #pasp_load (\n{body}\n)"


def merge_staged_rows(
    schema: str,
    table: str,
    columns: list[str],
    keys: list[str],
    identity: list[str],
) -> str:
    """Обновляет строки с тем же значением первой колонки и добавляет новые. Чужие строки не трогает."""
    qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
    key_match = " AND ".join(
        f"target.{quote_ident(name)} = source.{quote_ident(name)}" for name in keys
    )
    update_columns = [name for name in columns if name not in keys and name not in identity]
    quoted = ", ".join(quote_ident(name) for name in columns)
    sources = ", ".join(f"source.{quote_ident(name)}" for name in columns)
    lines = [
        f"MERGE {qualified} AS target",
        "USING #pasp_load AS source",
        f"ON {key_match}",
    ]
    if update_columns:
        assignments = ", ".join(
            f"{quote_ident(name)} = source.{quote_ident(name)}" for name in update_columns
        )
        lines.append(f"WHEN MATCHED THEN UPDATE SET {assignments}")
    lines.append(f"WHEN NOT MATCHED BY TARGET THEN INSERT ({quoted}) VALUES ({sources});")
    statement = "\n".join(lines)
    if any(name in identity for name in columns):
        return (
            f"SET IDENTITY_INSERT {qualified} ON;\n"
            f"{statement}\n"
            f"SET IDENTITY_INSERT {qualified} OFF;"
        )
    return statement
