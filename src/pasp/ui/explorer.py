"""Обозреватель объектов в духе левой панели SSMS.

База раскрывается отдельно: в память попадают имена, а столбцы — только у раскрытого объекта.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QStyle, QTreeWidget, QTreeWidgetItem

from pasp.application.workers import CatalogWorker, DetailWorker
from pasp.infrastructure.mssql import ConnParams

_KIND = Qt.ItemDataRole.UserRole
_SCHEMA = Qt.ItemDataRole.UserRole + 1
_NAME = Qt.ItemDataRole.UserRole + 2
_DB = Qt.ItemDataRole.UserRole + 3
_LOADED = Qt.ItemDataRole.UserRole + 4
_ONLINE = Qt.ItemDataRole.UserRole + 5

_EXPANDABLE = {"table", "view", "procedure", "function"}


class ObjectExplorer(QTreeWidget):
    checked_changed = pyqtSignal(int)
    status_message = pyqtSignal(str)
    database_activated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setAccessibleName("Обозреватель объектов")
        self.itemChanged.connect(self._on_item_changed)
        self.itemExpanded.connect(self._on_expanded)
        self.itemDoubleClicked.connect(self._on_double_clicked)
        self._params: ConnParams | None = None
        self._guard = False
        self._current_database = ""
        self._catalog_queue: list[str] = []
        self._detail_queue: list[tuple[str, str, str, str]] = []
        self._catalog_worker: CatalogWorker | None = None
        self._detail_worker: DetailWorker | None = None
        self._filter_text = ""
        style = self.style()
        self._icon_server = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._icon_folder = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._icon_database = style.standardIcon(QStyle.StandardPixmap.SP_DriveHDIcon)
        self._icon_table = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

    def set_params(self, params: ConnParams | None) -> None:
        self._params = params
        if params is None:
            self._catalog_queue.clear()
            self._detail_queue.clear()
            self.clear()

    def show_server(self, server: str, current: str, databases: list[dict]) -> None:
        self._current_database = current
        self._catalog_queue.clear()
        self._detail_queue.clear()
        self.setUpdatesEnabled(False)
        self.blockSignals(True)
        self.clear()
        root = QTreeWidgetItem([server or "SQL Server"])
        root.setData(0, _KIND, "server")
        root.setIcon(0, self._icon_server)
        databases_folder = self._folder("Базы данных", checkable=True)
        system_folder = self._folder("Системные базы данных", checkable=True)
        user_items: list[QTreeWidgetItem] = []
        for entry in databases:
            item = self._database_item(entry)
            if entry.get("system"):
                system_folder.addChild(item)
            else:
                user_items.append(item)
        databases_folder.addChild(system_folder)
        for item in user_items:
            databases_folder.addChild(item)
        root.addChild(databases_folder)
        self.addTopLevelItem(root)
        root.setExpanded(True)
        databases_folder.setExpanded(True)
        self.blockSignals(False)
        self.setUpdatesEnabled(True)
        self.focus_database(current)
        self.checked_changed.emit(0)

    def focus_database(self, name: str) -> None:
        self._current_database = name
        target = self._find_database(name)
        self._mark_current()
        if target is None:
            return
        parent = target.parent()
        if parent is not None:
            parent.setExpanded(True)
        databases = parent.parent() if parent is not None else None
        if databases is not None:
            databases.setExpanded(True)
        if target.data(0, _ONLINE) and target.data(0, _LOADED) is not True:
            target.setExpanded(True)
        self.scrollToItem(target)

    def apply_filter(self, text: str) -> None:
        self._filter_text = text
        folded = text.strip().casefold()
        for index in range(self.topLevelItemCount()):
            self._filter_item(self.topLevelItem(index), folded)

    def checked_tables(self) -> list[tuple[str, str, str]]:
        chosen: list[tuple[str, str, str]] = []

        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, _KIND) == "table" and item.checkState(0) == Qt.CheckState.Checked:
                chosen.append((str(item.data(0, _DB)), str(item.data(0, _SCHEMA)), str(item.data(0, _NAME))))
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return chosen

    def _database_item(self, entry: dict) -> QTreeWidgetItem:
        name = str(entry["name"])
        online = bool(entry.get("online", True))
        label = name if online else f"{name} (вне сети)"
        item = QTreeWidgetItem([label])
        item.setData(0, _KIND, "database")
        item.setData(0, _DB, name)
        item.setData(0, _ONLINE, online)
        item.setData(0, _LOADED, False)
        item.setIcon(0, self._icon_database)
        self._checkable(item)
        if online:
            stub = QTreeWidgetItem(["Загрузка…"])
            stub.setData(0, _KIND, "stub")
            item.addChild(stub)
        if name == self._current_database:
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
        return item

    def _folder(self, title: str, *, checkable: bool = False) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, _KIND, "folder")
        item.setIcon(0, self._icon_folder)
        if checkable:
            self._checkable(item)
        return item

    def _object_item(self, kind: str, database: str, schema: str, name: str, *, checkable: bool = False) -> QTreeWidgetItem:
        label = f"{schema}.{name}" if schema else name
        item = QTreeWidgetItem([label])
        item.setData(0, _KIND, kind)
        item.setData(0, _SCHEMA, schema)
        item.setData(0, _NAME, name)
        item.setData(0, _DB, database)
        item.setData(0, _LOADED, False)
        item.setIcon(0, self._icon_table)
        if checkable:
            self._checkable(item)
        if kind in _EXPANDABLE:
            stub = QTreeWidgetItem(["Загрузка…"])
            stub.setData(0, _KIND, "stub")
            item.addChild(stub)
        return item

    def _checkable(self, item: QTreeWidgetItem) -> None:
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(0, Qt.CheckState.Unchecked)

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if self._params is None:
            return
        kind = item.data(0, _KIND)
        loaded = item.data(0, _LOADED)
        if kind == "database":
            if not item.data(0, _ONLINE) or loaded is True or loaded == "loading":
                return
            item.setData(0, _LOADED, "loading")
            self._enqueue_catalog(str(item.data(0, _DB)))
            return
        if kind in _EXPANDABLE and loaded is not True and loaded != "loading":
            item.setData(0, _LOADED, "loading")
            self._enqueue_detail(
                str(item.data(0, _DB)),
                str(item.data(0, _SCHEMA) or ""),
                str(item.data(0, _NAME)),
                str(kind),
            )

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.data(0, _KIND) == "database" and item.data(0, _ONLINE):
            self.database_activated.emit(str(item.data(0, _DB)))

    def _enqueue_catalog(self, database: str) -> None:
        if database not in self._catalog_queue:
            self._catalog_queue.append(database)
        self._pump_catalog()

    def _pump_catalog(self) -> None:
        if self._params is None or (self._catalog_worker is not None and self._catalog_worker.isRunning()):
            return
        if not self._catalog_queue:
            return
        database = self._catalog_queue.pop(0)
        worker = CatalogWorker(self._params, database)
        worker.ready.connect(self._on_catalog)
        worker.failed.connect(self._on_catalog_failed)
        worker.finished.connect(self._pump_catalog)
        worker.finished.connect(worker.deleteLater)
        self._catalog_worker = worker
        worker.start()

    def _enqueue_detail(self, database: str, schema: str, name: str, kind: str) -> None:
        key = (database, schema, name, kind)
        if key not in self._detail_queue:
            self._detail_queue.append(key)
        self._pump_detail()

    def _pump_detail(self) -> None:
        if self._params is None or (self._detail_worker is not None and self._detail_worker.isRunning()):
            return
        if not self._detail_queue:
            return
        database, schema, name, kind = self._detail_queue.pop(0)
        worker = DetailWorker(self._params, database, schema, name, kind)
        worker.ready.connect(self._on_detail)
        worker.failed.connect(self._on_detail_failed)
        worker.finished.connect(self._pump_detail)
        worker.finished.connect(worker.deleteLater)
        self._detail_worker = worker
        worker.start()

    def _on_catalog(self, database: str, catalog: dict) -> None:
        item = self._find_database(database)
        if item is None or self._params is None:
            return
        self.setUpdatesEnabled(False)
        self.blockSignals(True)
        item.takeChildren()
        self._fill_catalog(item, database, catalog)
        item.setData(0, _LOADED, True)
        if self._ancestor_checked(item):
            self._check_tables(item)
        self.blockSignals(False)
        self.setUpdatesEnabled(True)
        tables = self._child_by_title(item, "Таблицы")
        if tables is not None and database == self._current_database:
            tables.setExpanded(True)
        self.apply_filter(self._filter_text)
        self.checked_changed.emit(len(self.checked_tables()))

    def _on_catalog_failed(self, database: str, message: str) -> None:
        item = self._find_database(database)
        if item is not None:
            item.setData(0, _LOADED, False)
            item.takeChildren()
            failed = QTreeWidgetItem(["Не удалось загрузить"])
            failed.setData(0, _KIND, "error")
            item.addChild(failed)
        self.status_message.emit(f"{database}: {message}")

    def _fill_catalog(self, database_item: QTreeWidgetItem, database: str, catalog: dict) -> None:
        tables = catalog.get("tables", [])
        views = catalog.get("views", [])
        procedures = catalog.get("procedures", [])
        functions = catalog.get("functions", [])
        user_tables = self._folder("Таблицы", checkable=True)
        system_tables = self._folder("Системные таблицы", checkable=True)
        for entry in tables:
            node = self._object_item("table", database, entry["schema"], entry["name"], checkable=True)
            (system_tables if entry.get("system") else user_tables).addChild(node)
        user_tables.addChild(system_tables)
        database_item.addChild(user_tables)

        user_views = self._folder("Представления")
        system_views = self._folder("Системные представления")
        for entry in views:
            node = self._object_item("view", database, entry["schema"], entry["name"])
            (system_views if entry.get("system") else user_views).addChild(node)
        user_views.addChild(system_views)
        database_item.addChild(user_views)

        synonyms = self._folder("Синонимы")
        for entry in catalog.get("synonyms", []):
            synonyms.addChild(self._object_item("synonym", database, entry["schema"], entry["name"]))
        database_item.addChild(synonyms)

        programming = self._folder("Программирование")
        user_procs = self._folder("Хранимые процедуры")
        system_procs = self._folder("Системные хранимые процедуры")
        for entry in procedures:
            node = self._object_item("procedure", database, entry["schema"], entry["name"])
            (system_procs if entry.get("system") else user_procs).addChild(node)
        user_procs.addChild(system_procs)
        programming.addChild(user_procs)

        functions_folder = self._folder("Функции")
        grouped = {
            "table": self._folder("Функции, возвращающие табличное значение"),
            "scalar": self._folder("Скалярные функции"),
            "aggregate": self._folder("Агрегатные функции"),
        }
        system_functions = self._folder("Системные функции")
        for entry in functions:
            node = self._object_item("function", database, entry["schema"], entry["name"])
            if entry.get("system"):
                system_functions.addChild(node)
            else:
                grouped.get(entry.get("group"), grouped["scalar"]).addChild(node)
        for key in ("table", "scalar", "aggregate"):
            functions_folder.addChild(grouped[key])
        functions_folder.addChild(system_functions)
        programming.addChild(functions_folder)

        trigger_folder = self._folder("Триггеры базы данных")
        for name in catalog.get("triggers", []):
            trigger_folder.addChild(self._object_item("trigger", database, "", name))
        programming.addChild(trigger_folder)

        types_folder = self._folder("Типы")
        data_types = self._folder("Пользовательские типы данных")
        table_types = self._folder("Пользовательские типы таблиц")
        for entry in catalog.get("types", []):
            node = self._object_item("type", database, entry["schema"], entry["name"])
            (table_types if entry.get("table_type") else data_types).addChild(node)
        types_folder.addChild(data_types)
        types_folder.addChild(table_types)
        programming.addChild(types_folder)
        database_item.addChild(programming)

    def _on_detail(self, database: str, schema: str, name: str, kind: str, details: dict) -> None:
        item = self._find_object(database, schema, name, kind)
        if item is None:
            return
        self.blockSignals(True)
        item.takeChildren()
        if kind in {"procedure", "function"}:
            self._add_leaf_folder(item, "Параметры", details.get("parameters", []))
        else:
            self._add_leaf_folder(item, "Столбцы", details.get("columns", []))
        if kind == "table":
            self._add_leaf_folder(item, "Ключи", details.get("keys", []))
            self._add_leaf_folder(item, "Ограничения", details.get("checks", []))
            self._add_leaf_folder(item, "Триггеры", details.get("triggers", []))
            self._add_leaf_folder(item, "Индексы", details.get("indexes", []))
            self._add_leaf_folder(item, "Статистика", details.get("stats", []))
        item.setData(0, _LOADED, True)
        self.blockSignals(False)

    def _on_detail_failed(self, database: str, schema: str, name: str, kind: str, message: str) -> None:
        item = self._find_object(database, schema, name, kind)
        if item is not None:
            item.setData(0, _LOADED, False)
        self.status_message.emit(f"{schema}.{name}: {message}" if schema else f"{name}: {message}")

    def _add_leaf_folder(self, parent: QTreeWidgetItem, title: str, labels: list[str]) -> None:
        folder = self._folder(title)
        for label in labels:
            leaf = QTreeWidgetItem([label])
            leaf.setData(0, _KIND, "leaf")
            folder.addChild(leaf)
        parent.addChild(folder)

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._guard:
            return
        self._guard = True
        try:
            if item.data(0, _KIND) == "table":
                self._sync_ancestors(item.parent())
            else:
                state = item.checkState(0)
                if state != Qt.CheckState.PartiallyChecked:
                    self._check_down(item, state)
                    self._sync_ancestors(item.parent())
        finally:
            self._guard = False
        self.checked_changed.emit(len(self.checked_tables()))

    def _check_down(self, item: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(0, state)
            self._check_down(child, state)

    def _check_tables(self, item: QTreeWidgetItem) -> None:
        for index in range(item.childCount()):
            child = item.child(index)
            if child.data(0, _KIND) == "table":
                child.setCheckState(0, Qt.CheckState.Checked)
            else:
                self._check_tables(child)
        self._sync_ancestors(item)

    def _sync_ancestors(self, item: QTreeWidgetItem | None) -> None:
        while item is not None:
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                states = self._table_states(item)
                if not states:
                    pass
                elif all(states):
                    item.setCheckState(0, Qt.CheckState.Checked)
                elif any(states):
                    item.setCheckState(0, Qt.CheckState.PartiallyChecked)
                else:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
            item = item.parent()

    def _table_states(self, item: QTreeWidgetItem) -> list[bool]:
        states: list[bool] = []
        for index in range(item.childCount()):
            child = item.child(index)
            if child.data(0, _KIND) == "table":
                states.append(child.checkState(0) == Qt.CheckState.Checked)
            else:
                states.extend(self._table_states(child))
        return states

    def _ancestor_checked(self, item: QTreeWidgetItem) -> bool:
        node: QTreeWidgetItem | None = item
        while node is not None:
            if node.flags() & Qt.ItemFlag.ItemIsUserCheckable and node.checkState(0) == Qt.CheckState.Checked:
                return True
            node = node.parent()
        return False

    def _filter_item(self, item: QTreeWidgetItem, text: str) -> bool:
        own = bool(text) and text in item.text(0).casefold()
        if own:
            self._show_branch(item)
            return True
        child_match = False
        for index in range(item.childCount()):
            if self._filter_item(item.child(index), text):
                child_match = True
        visible = not text or child_match
        item.setHidden(not visible)
        return visible

    def _show_branch(self, item: QTreeWidgetItem) -> None:
        item.setHidden(False)
        for index in range(item.childCount()):
            self._show_branch(item.child(index))

    def _mark_current(self) -> None:
        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, _KIND) == "database":
                font = item.font(0)
                font.setBold(item.data(0, _DB) == self._current_database)
                item.setFont(0, font)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))

    def _find_database(self, name: str) -> QTreeWidgetItem | None:
        found: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            if item.data(0, _KIND) == "database" and item.data(0, _DB) == name:
                found.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return found[0] if found else None

    def _find_object(self, database: str, schema: str, name: str, kind: str) -> QTreeWidgetItem | None:
        found: list[QTreeWidgetItem] = []

        def walk(item: QTreeWidgetItem) -> None:
            item_kind = item.data(0, _KIND)
            if (
                item.data(0, _DB) == database
                and item.data(0, _SCHEMA) == schema
                and item.data(0, _NAME) == name
                and (not kind or item_kind == kind)
            ):
                found.append(item)
            for index in range(item.childCount()):
                walk(item.child(index))

        for index in range(self.topLevelItemCount()):
            walk(self.topLevelItem(index))
        return found[0] if found else None

    def _child_by_title(self, item: QTreeWidgetItem, title: str) -> QTreeWidgetItem | None:
        for index in range(item.childCount()):
            child = item.child(index)
            if child.text(0) == title:
                return child
        return None
