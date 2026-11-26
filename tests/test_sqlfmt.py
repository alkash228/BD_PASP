import unittest
from datetime import date, datetime, time
from decimal import Decimal
from io import StringIO

from pasp.sqlfmt import (
    ColumnMeta,
    create_table_sql,
    format_cell,
    format_type,
    insert_column_names,
    merge_staged_rows,
    quote_ident,
    retarget_export_script,
    script_target,
    split_batches,
    sql_literal,
    staging_table_sql,
    write_insert_batch,
)


class SqlFormatTests(unittest.TestCase):
    def test_quote_ident_escapes_bracket(self):
        self.assertEqual(quote_ident("a]b"), "[a]]b]")

    def test_literals(self):
        self.assertEqual(sql_literal(None), "NULL")
        self.assertEqual(sql_literal(True), "1")
        self.assertEqual(sql_literal(False), "0")
        self.assertEqual(sql_literal("O'Brien"), "N'O''Brien'")
        self.assertEqual(sql_literal(b"\x00\xff"), "0x00ff")
        self.assertEqual(sql_literal(Decimal("10.50")), "10.50")
        self.assertEqual(sql_literal(date(2024, 1, 2)), "'2024-01-02'")
        self.assertTrue(sql_literal(datetime(2024, 1, 2, 3, 4, 5)).startswith("'2024-01-02T03:04:05"))
        self.assertEqual(sql_literal(time(1, 2, 3)), "'01:02:03'")
        self.assertEqual(sql_literal(float("nan")), "NULL")

    def test_cell_truncates_binary(self):
        text = format_cell(b"x" * 40)
        self.assertIn("байт", text)
        self.assertLess(len(text), 80)

    def test_types(self):
        self.assertEqual(format_type("nvarchar", -1, 0, 0), "nvarchar(max)")
        self.assertEqual(format_type("nvarchar", 100, 0, 0), "nvarchar(50)")
        self.assertEqual(format_type("decimal", 9, 10, 2), "decimal(10,2)")
        self.assertEqual(format_type("int", 4, 10, 0), "int")

    def test_go_batches(self):
        sql = "SELECT 1\nGO\nSELECT 2\nGO 2\n"
        self.assertEqual(split_batches(sql), ["SELECT 1", "SELECT 2", "SELECT 2"])

    def test_insert_batch_is_reloadable(self):
        handle = StringIO()
        write_insert_batch(
            handle,
            "dbo",
            "Item",
            ["Id", "Name"],
            [(1, "a'b")],
            identity=True,
        )
        script = handle.getvalue()
        self.assertIn("SET IDENTITY_INSERT [dbo].[Item] ON;", script)
        self.assertIn("N'a''b'", script)
        self.assertIn("COMMIT TRANSACTION;", script)
        self.assertTrue(script.endswith("GO\n"))

    def test_create_skips_computed(self):
        script = create_table_sql(
            "dbo",
            "Item",
            [
                ColumnMeta("Id", "int", False, True, 1, 1, False, "int"),
                ColumnMeta("Calc", "int", True, False, None, None, True, "int"),
            ],
        )
        self.assertIn("[Id] int IDENTITY(1,1) NOT NULL", script)
        self.assertNotIn("Calc", script)
        self.assertIn("IF OBJECT_ID", script)


class RetargetExportTests(unittest.TestCase):
    def _exported(self) -> str:
        handle = StringIO()
        handle.write(
            create_table_sql(
                "dbo",
                "Item",
                [ColumnMeta("Id", "int", False, True, 1, 1, False, "int")],
            )
        )
        write_insert_batch(handle, "dbo", "Item", ["Id", "Name"], [(1, "a'b")], identity=True)
        return handle.getvalue()

    def test_script_names_the_exported_table(self):
        self.assertEqual(script_target(self._exported()), ("dbo", "Item"))

    def test_rows_are_updated_by_key_without_deleting(self):
        script = retarget_export_script(self._exported(), "archive", "Orders")
        self.assertNotIn("DELETE", script)
        self.assertIn("INSERT INTO #pasp_load ([Id], [Name]) VALUES", script)
        self.assertNotIn("INSERT INTO [archive].[Orders]", script)
        self.assertNotIn("IDENTITY_INSERT", script)
        self.assertIn("CREATE TABLE [archive].[Orders]", script)
        self.assertIn("OBJECT_ID(N'archive.Orders'", script)
        self.assertNotIn("[dbo].[Item]", script)
        self.assertIn("N'a''b'", script)
        columns = insert_column_names(script)
        self.assertEqual(columns, ["Id", "Name"])
        staged = staging_table_sql([("Id", "int"), ("Name", "nvarchar(100)")])
        self.assertIn("[Id] int NULL", staged)
        self.assertIn("[Name] nvarchar(100) NULL", staged)
        merge = merge_staged_rows("archive", "Orders", columns, columns[:1], ["Id"])
        self.assertIn("MERGE [archive].[Orders] AS target", merge)
        self.assertIn("USING #pasp_load AS source", merge)
        self.assertIn("ON target.[Id] = source.[Id]", merge)
        self.assertIn("WHEN MATCHED THEN UPDATE SET [Name] = source.[Name]", merge)
        self.assertNotIn("UPDATE SET [Id]", merge)
        self.assertIn("WHEN NOT MATCHED BY TARGET THEN INSERT ([Id], [Name]) VALUES (source.[Id], source.[Name]);", merge)
        self.assertNotIn("DELETE", merge)
        self.assertNotIn("WHEN NOT MATCHED BY SOURCE", merge)
        self.assertIn("SET IDENTITY_INSERT [archive].[Orders] ON;", merge)

    def test_same_table_is_not_cleared(self):
        script = retarget_export_script(self._exported(), "dbo", "Item")
        self.assertNotIn("DELETE", script)
        self.assertIn("INSERT INTO #pasp_load ([Id], [Name]) VALUES", script)

    def test_file_without_table_export_is_rejected(self):
        with self.assertRaises(ValueError):
            retarget_export_script("SELECT 1\nGO\n", "dbo", "Item")


if __name__ == "__main__":
    unittest.main()
