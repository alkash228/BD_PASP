from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import QElapsedTimer, QSettings, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from pasp.application.schedule import describe_job, due_slot, load_jobs, update_job
from pasp.application.workers import KillWorker, MetaWorker, QueryExportWorker, QueryWorker
from pasp.infrastructure.mssql import ConnParams, connect
from pasp.infrastructure.profiles import load_profiles
from pasp.infrastructure.secrets import load_password
from pasp.sqlfmt import safe_filename
from pasp.ui.connect_dialog import ConnectDialog
from pasp.ui.explorer import ObjectExplorer
from pasp.ui.export_dialog import ExportDialog
from pasp.ui.results_model import ResultModel
from pasp.ui.saved_queries import SavedQueriesDialog
from pasp.ui.schedule_dialog import ScheduleDialog
from pasp.ui.sql_edit import SqlEdit


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setFrameShape(QFrame.Shape.NoFrame)
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return frame


def _pane_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PaneTitle")
    return label


def _button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _format_elapsed(milliseconds: int) -> str:
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f} с"
    minutes, whole = divmod(int(seconds), 60)
    return f"{minutes}:{whole:02d}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("BD PASP")
        self.resize(1280, 800)
        self.setMinimumSize(960, 640)
        self._params: ConnParams | None = None
        self._query_worker: QueryWorker | None = None
        self._meta_worker: MetaWorker | None = None
        self._killer: KillWorker | None = None
        self._query_running = False
        self._meta_running = False
        self._sized = False
        self._tree_guard = False
        self._meta_pending = False
        self._quitting = False
        self._schedule_worker: QueryExportWorker | None = None
        self._schedule_job_id = ""
        self._schedule_slot = ""

        self._build()
        self._restore()
        self._setup_tray()
        self._setup_schedule()
        self._refresh_actions()

    def _build(self) -> None:
        accent = QFrame()
        accent.setObjectName("AccentBar")
        accent.setFixedHeight(3)

        self.connect_button = _button("Подключить")
        self.disconnect_button = _button("Отключить")
        self.run_button = _button("Выполнить")
        self.cancel_button = _button("Отмена")
        self.export_button = _button("Выгрузить")
        self.queries_button = _button("Запросы")
        self.schedule_button = _button("Расписание")
        self.connect_button.clicked.connect(self._open_connect)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.run_button.clicked.connect(self._execute)
        self.cancel_button.clicked.connect(self._cancel)
        self.export_button.clicked.connect(self._export)
        self.queries_button.clicked.connect(self._open_queries)
        self.schedule_button.clicked.connect(self._open_schedule)
        self.run_button.setToolTip("F5 или Ctrl+Enter. Если выделен фрагмент, выполняется только он.")
        self.cancel_button.setToolTip("Остановить текущий запрос (Esc)")

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(12, 12, 12, 0)
        toolbar.setSpacing(8)
        for button in (
            self.connect_button,
            self.disconnect_button,
            self.run_button,
            self.cancel_button,
            self.export_button,
            self.queries_button,
            self.schedule_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)

        self.db_combo = QComboBox()
        self.db_combo.setAccessibleName("База данных")
        self.db_combo.currentIndexChanged.connect(self._database_changed)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Фильтр объектов")
        self.filter_edit.setAccessibleName("Фильтр объектов")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.filter_edit.textChanged.connect(lambda _text: self._filter_timer.start())
        self.checked_label = QLabel("Отмечено: 0")
        self.checked_label.setObjectName("Muted")
        self.tree = ObjectExplorer()
        self.tree.checked_changed.connect(self._on_checked)
        self.tree.status_message.connect(self._log)
        self.tree.database_activated.connect(self._activate_database)
        self.objects_hint = QLabel("Подключитесь, чтобы увидеть базы, папки и таблицы.")
        self.objects_hint.setObjectName("EmptyHint")
        self.objects_hint.setWordWrap(True)

        side_title = QLabel("Объекты")
        side_title.setObjectName("SectionTitle")
        side_layout = QVBoxLayout()
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(8)
        side_layout.addWidget(side_title)
        side_layout.addWidget(self.db_combo)
        side_layout.addWidget(self.filter_edit)
        side_layout.addWidget(self.tree, 1)
        side_layout.addWidget(self.objects_hint)
        side_layout.addWidget(self.checked_label)
        side = _card()
        side.setLayout(side_layout)

        self.editor = SqlEdit()
        self.editor.setAccessibleName("Редактор SQL")
        editor_layout = QVBoxLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(_pane_title("Запрос"))
        editor_layout.addWidget(self.editor)
        editor_card = _card()
        editor_card.setLayout(editor_layout)

        self.model = ResultModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.empty_results = QLabel("Результат запроса появится здесь")
        self.empty_results.setObjectName("EmptyHint")
        self.empty_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.results_stack = QStackedWidget()
        self.results_stack.addWidget(self.empty_results)
        self.results_stack.addWidget(self.table)
        results_layout = QVBoxLayout()
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)
        results_layout.addWidget(_pane_title("Результат"))
        results_layout.addWidget(self.results_stack)
        results_card = _card()
        results_card.setLayout(results_layout)

        self.messages = QPlainTextEdit()
        self.messages.setObjectName("MessageLog")
        self.messages.setReadOnly(True)
        self.messages.setMaximumBlockCount(400)
        self.messages.setPlaceholderText("Сообщения сервера")
        self.messages.setAccessibleName("Сообщения")
        messages_layout = QVBoxLayout()
        messages_layout.setContentsMargins(0, 0, 0, 0)
        messages_layout.setSpacing(0)
        messages_layout.addWidget(_pane_title("Сообщения"))
        messages_layout.addWidget(self.messages)
        messages_card = _card()
        messages_card.setLayout(messages_layout)

        self.vertical = QSplitter(Qt.Orientation.Vertical)
        self.vertical.addWidget(editor_card)
        self.vertical.addWidget(results_card)
        self.vertical.addWidget(messages_card)
        self.vertical.setStretchFactor(0, 3)
        self.vertical.setStretchFactor(1, 4)
        self.vertical.setStretchFactor(2, 1)
        self.vertical.setChildrenCollapsible(False)

        self.horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.horizontal.addWidget(side)
        self.horizontal.addWidget(self.vertical)
        self.horizontal.setStretchFactor(0, 0)
        self.horizontal.setStretchFactor(1, 1)
        self.horizontal.setChildrenCollapsible(False)
        self.horizontal.setSizes([280, 1000])

        work = QVBoxLayout()
        work.setContentsMargins(12, 12, 12, 12)
        work.setSpacing(0)
        work.addWidget(self.horizontal, 1)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(accent)
        root.addLayout(toolbar)
        root.addLayout(work, 1)
        self.setCentralWidget(central)

        self._conn_label = QLabel("Нет подключения")
        self._state_label = QLabel("Готово")
        self._rows_label = QLabel("")
        self._time_label = QLabel("")
        self._progress = QProgressBar()
        self._progress.setFixedWidth(160)
        self._progress.setTextVisible(False)
        self._progress.hide()
        status = QStatusBar()
        status.addWidget(self._conn_label, 1)
        status.addPermanentWidget(self._state_label)
        status.addPermanentWidget(self._rows_label)
        status.addPermanentWidget(self._time_label)
        status.addPermanentWidget(self._progress)
        self.setStatusBar(status)

        self._clock = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._tick_clock)

        QShortcut(QKeySequence("F5"), self, self._execute)
        QShortcut(QKeySequence("Ctrl+Return"), self, self._execute)
        QShortcut(QKeySequence("Esc"), self, self._cancel)

    def _restore(self) -> None:
        settings = QSettings("PASP", "BD_PASP")
        geometry = settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        horizontal = settings.value("horizontal")
        vertical = settings.value("vertical")
        if horizontal is not None:
            self.horizontal.restoreState(horizontal)
        if vertical is not None:
            self.vertical.restoreState(vertical)
        self._move_onto_screen()

    def _move_onto_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        frame = self.frameGeometry()
        too_small = frame.width() < 400 or frame.height() < 300
        if too_small or not area.intersects(frame):
            self.resize(min(1280, max(960, area.width() - 80)), min(800, max(640, area.height() - 80)))
            frame = self.frameGeometry()
            frame.moveCenter(area.center())
            self.move(frame.topLeft())

    def _save_settings(self) -> None:
        settings = QSettings("PASP", "BD_PASP")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("horizontal", self.horizontal.saveState())
        settings.setValue("vertical", self.vertical.saveState())

    def closeEvent(self, event) -> None:
        if not self._quitting and self._tray is not None and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._notify_hidden()
            return
        self._stop_schedule_worker()
        self._shutdown_query()
        self._save_settings()
        super().closeEvent(event)
        QApplication.quit()

    def _refresh_actions(self) -> None:
        connected = self._params is not None
        self.connect_button.setObjectName("" if connected else "PrimaryButton")
        self.run_button.setObjectName("PrimaryButton" if connected else "")
        for button in (self.connect_button, self.run_button):
            button.style().unpolish(button)
            button.style().polish(button)
        self.run_button.setEnabled(connected and not self._query_running)
        self.cancel_button.setEnabled(self._query_running)
        self.disconnect_button.setEnabled(connected and not self._query_running)
        self.db_combo.setEnabled(connected and not self._query_running and self.db_combo.count() > 0)
        self.export_button.setEnabled(connected and not self._query_running and bool(self.tree.checked_tables()))
        self.queries_button.setEnabled(not self._query_running)
        self.objects_hint.setVisible(not connected or self.tree.topLevelItemCount() == 0)

    def _open_connect(self) -> None:
        if self._query_running:
            return
        dialog = ConnectDialog(self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._params = dialog.params()
        if dialog.password_warning:
            self._log(dialog.password_warning)
        self._show_connection()
        self._load_meta()

    def _disconnect(self) -> None:
        if self._query_running:
            return
        self._shutdown_query()
        self._params = None
        self.db_combo.blockSignals(True)
        self.db_combo.clear()
        self.db_combo.blockSignals(False)
        self.tree.set_params(None)
        self.checked_label.setText("Отмечено: 0")
        self.model.clear()
        self.results_stack.setCurrentWidget(self.empty_results)
        self._rows_label.clear()
        self._conn_label.setText("Нет подключения")
        self.setWindowTitle("BD PASP")
        self._refresh_actions()

    def _show_connection(self) -> None:
        if self._params is None:
            return
        database = self._params.database or "—"
        self._conn_label.setText(f"{self._params.server}  ·  {database}")
        self.setWindowTitle(f"BD PASP — {self._params.server}")

    def _load_meta(self) -> None:
        if self._params is None:
            return
        if self._meta_worker is not None and self._meta_worker.isRunning():
            self._meta_pending = True
            return
        self._meta_pending = False
        self._meta_running = True
        self._state_label.setText("Загрузка объектов…")
        self._progress.setRange(0, 0)
        self._progress.show()
        worker = MetaWorker(self._params)
        worker.ready.connect(self._on_meta)
        worker.failed.connect(self._on_meta_failed)
        worker.finished.connect(self._meta_finished)
        worker.finished.connect(worker.deleteLater)
        self._meta_worker = worker
        worker.start()

    def _on_meta(self, database: str, databases: list) -> None:
        if self._meta_pending or self._params is None:
            return
        if database and database != self._params.database:
            self._params = ConnParams(
                server=self._params.server,
                database=database,
                auth=self._params.auth,
                username=self._params.username,
                password=self._params.password,
                trust_cert=self._params.trust_cert,
            )
        names = [str(entry["name"]) for entry in databases if entry.get("online", True)]
        self._fill_databases(names, database)
        self.tree.set_params(self._params)
        self.tree.show_server(self._params.server, database or "", databases)
        self._show_connection()
        self._refresh_actions()

    def _on_meta_failed(self, message: str) -> None:
        if self._meta_pending:
            return
        self._log(message)
        self._refresh_actions()

    def _meta_finished(self) -> None:
        pending = self._meta_pending
        self._meta_pending = False
        self._meta_running = False
        if not self._query_running and not pending:
            self._progress.hide()
            self._state_label.setText("Готово")
        if pending:
            QTimer.singleShot(0, self._load_meta)

    def _fill_databases(self, databases: list[str], current: str) -> None:
        self.db_combo.blockSignals(True)
        self.db_combo.clear()
        self.db_combo.addItems(databases)
        index = self.db_combo.findText(current)
        if index >= 0:
            self.db_combo.setCurrentIndex(index)
        self.db_combo.blockSignals(False)

    def _database_changed(self, index: int) -> None:
        if self._params is None or index < 0 or self._query_running:
            return
        name = self.db_combo.itemText(index)
        if not name or name == self._params.database:
            return
        self._params = ConnParams(
            server=self._params.server,
            database=name,
            auth=self._params.auth,
            username=self._params.username,
            password=self._params.password,
            trust_cert=self._params.trust_cert,
        )
        self.tree.set_params(self._params)
        self._show_connection()
        self.tree.focus_database(name)

    def _activate_database(self, name: str) -> None:
        index = self.db_combo.findText(name)
        if index >= 0 and index != self.db_combo.currentIndex():
            self.db_combo.setCurrentIndex(index)
            return
        if self._params is not None and name != self._params.database:
            self._database_changed(index if index >= 0 else -1)

    def _apply_filter(self) -> None:
        self.tree.apply_filter(self.filter_edit.text())

    def _on_checked(self, count: int) -> None:
        self.checked_label.setText(f"Отмечено: {count}")
        self._refresh_actions()

    def _execute(self) -> None:
        if self._params is None or self._query_running:
            return
        cursor = self.editor.textCursor()
        sql = cursor.selectedText() if cursor.hasSelection() else self.editor.toPlainText()
        sql = sql.replace("\u2029", "\n").strip()
        if not sql:
            self._log("Введите запрос.")
            return
        self.model.clear()
        self.results_stack.setCurrentWidget(self.empty_results)
        self._rows_label.clear()
        self.messages.clear()
        self._sized = False
        self.table.setSortingEnabled(False)
        self._query_running = True
        self._state_label.setText("Выполняется…")
        self._progress.setRange(0, 0)
        self._progress.show()
        self._clock.start()
        self._tick.start()
        self._refresh_actions()
        worker = QueryWorker(self._params, sql)
        worker.columns_ready.connect(self._on_columns)
        worker.batch_ready.connect(self._on_batch)
        worker.message.connect(self._log)
        worker.succeeded.connect(self._on_query_ok)
        worker.failed.connect(self._on_query_failed)
        worker.finished.connect(self._query_finished)
        worker.finished.connect(worker.deleteLater)
        self._query_worker = worker
        worker.start()

    def _on_columns(self, columns: list[str]) -> None:
        self._sized = False
        self.table.setSortingEnabled(False)
        self.model.set_columns(columns)
        self.results_stack.setCurrentWidget(self.table)
        self._rows_label.setText("0 строк")

    def _on_batch(self, rows: list[list[str]]) -> None:
        self.table.setUpdatesEnabled(False)
        try:
            self.model.add_rows(rows)
        finally:
            self.table.setUpdatesEnabled(True)
        if not self._sized and self.model.columnCount():
            self._size_columns()
            self._sized = True
        self._rows_label.setText(f"{self.model.rowCount()} строк")
        worker = self._query_worker
        if worker is not None:
            worker.ack()

    def _size_columns(self) -> None:
        metrics = self.table.fontMetrics()
        limit = min(self.model.columnCount(), 40)
        for index in range(limit):
            title = str(self.model.headerData(index, Qt.Orientation.Horizontal) or "")
            width = metrics.horizontalAdvance(title) + 28
            self.table.setColumnWidth(index, min(280, max(72, width)))

    def _on_query_ok(self, rows: int, truncated: bool) -> None:
        if rows and not truncated:
            self._log(f"Готово за {_format_elapsed(self._clock.elapsed())}.")
        self._finish_query()

    def _on_query_failed(self, message: str) -> None:
        self._log(message)
        self._finish_query()

    def _query_finished(self) -> None:
        if self._query_running:
            self._finish_query()

    def _finish_query(self) -> None:
        if not self._query_running and not self._tick.isActive():
            return
        self._query_running = False
        self._tick.stop()
        if self._clock.isValid():
            self._time_label.setText(_format_elapsed(self._clock.elapsed()))
        if not self._meta_running:
            self._progress.hide()
            self._state_label.setText("Готово")
        self.table.setSortingEnabled(self.model.rowCount() > 0)
        self._refresh_actions()

    def _tick_clock(self) -> None:
        if self._query_running:
            self._time_label.setText(_format_elapsed(self._clock.elapsed()))

    def _cancel(self) -> None:
        worker = self._query_worker
        if worker is None or not worker.isRunning():
            return
        worker.stop()
        self._state_label.setText("Отмена…")
        spid = worker.spid
        if spid and self._params is not None:
            killer = KillWorker(self._params, spid)
            killer.failed.connect(lambda message: self._log("Не удалось прервать сессию: " + message))
            killer.finished.connect(killer.deleteLater)
            self._killer = killer
            killer.start()

    def _shutdown_query(self) -> None:
        worker = self._query_worker
        if worker is None or not worker.isRunning():
            return
        spid = worker.spid
        worker.stop()
        if spid and self._params is not None:
            connection = None
            try:
                connection = connect(self._params, login_timeout=3, query_timeout=3)
                cursor = connection.cursor()
                try:
                    cursor.execute(f"KILL {int(spid)}")
                finally:
                    cursor.close()
            except Exception:
                pass
            finally:
                if connection is not None:
                    connection.close()
        worker.wait(2000)

    def _log(self, text: str) -> None:
        self.messages.appendPlainText(text)

    def _setup_tray(self) -> None:
        self._tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        menu = QMenu(self)
        menu.addAction("Открыть", self._show_from_tray)
        menu.addAction("Расписание", self._open_schedule_from_tray)
        menu.addSeparator()
        menu.addAction("Закрыть", self._quit_from_tray)
        tray = QSystemTrayIcon(self._make_tray_icon(), self)
        tray.setToolTip("BD PASP")
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self._tray = tray
        self._tray_menu = menu

    def _make_tray_icon(self) -> QIcon:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0F6CBD"))
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.setPen(QColor("#FFFFFF"))
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setPixelSize(16)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), "P")
        painter.end()
        icon = QIcon(pixmap)
        self.setWindowIcon(icon)
        return icon

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _open_schedule_from_tray(self) -> None:
        self._show_from_tray()
        self._open_schedule()

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self.close()

    def _notify_hidden(self) -> None:
        settings = QSettings("PASP", "BD_PASP")
        if str(settings.value("trayHintShown", "false")).lower() == "true":
            return
        settings.setValue("trayHintShown", True)
        if self._tray is not None:
            self._tray.showMessage(
                "BD PASP",
                "Окно скрыто. Программа остаётся в значках и выполняет расписание. Правый щелчок: открыть или закрыть.",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _setup_schedule(self) -> None:
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(20000)
        self._schedule_timer.timeout.connect(self._check_schedule)
        self._schedule_timer.start()
        QTimer.singleShot(3000, self._check_schedule)

    def _check_schedule(self) -> None:
        if self._schedule_worker is not None and self._schedule_worker.isRunning():
            return
        now = datetime.now()
        for job in load_jobs():
            slot = due_slot(job, now)
            if slot:
                self._start_scheduled(job.id, slot, manual=False)
                return

    def _open_schedule(self) -> None:
        dialog = ScheduleDialog(self)
        dialog.run_requested.connect(self._run_job_now)
        dialog.exec()

    def _run_job_now(self, job_id: str) -> None:
        self._start_scheduled(job_id, "", manual=True)

    def _start_scheduled(self, job_id: str, slot: str, *, manual: bool) -> None:
        if self._schedule_worker is not None and self._schedule_worker.isRunning():
            self._log("Уже выполняется другое задание расписания.")
            return
        job = next((item for item in load_jobs() if item.id == job_id), None)
        if job is None:
            return
        folder = job.output_dir.strip()
        if not folder:
            self._report_schedule(job.name, "Не указана папка выгрузки.", failed=True, job_id=job.id, slot=slot, manual=manual)
            return
        try:
            params = self._params_for_profile(job.profile_id)
        except RuntimeError as exc:
            self._report_schedule(job.name, str(exc), failed=True, job_id=job.id, slot=slot, manual=manual)
            return
        if params is None:
            self._report_schedule(job.name, "Подключение для задания не найдено.", failed=True, job_id=job.id, slot=slot, manual=manual)
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = safe_filename(job.target_schema, job.target_table).replace(".sql", f"_{stamp}.sql")
        destination = Path(folder) / filename
        self._schedule_job_id = job.id
        self._schedule_slot = slot
        self._schedule_manual = manual
        self._schedule_mode = job.mode
        worker = QueryExportWorker(
            params,
            job.sql,
            job.target_schema,
            job.target_table,
            destination,
            job.identity,
        )
        worker.succeeded.connect(self._schedule_succeeded)
        worker.failed.connect(self._schedule_failed)
        worker.finished.connect(worker.deleteLater)
        self._schedule_worker = worker
        self._log(f"Расписание «{job.name}»: {describe_job(job)}. Файл: {destination}")
        worker.start()

    def _params_for_profile(self, profile_id: str) -> ConnParams | None:
        for profile in load_profiles():
            if profile.id != profile_id:
                continue
            password = load_password(profile.id) if profile.auth == "sql" else ""
            if profile.auth == "sql" and not password:
                raise RuntimeError("Для профиля не сохранён пароль. Подключитесь и включите «Запомнить пароль».")
            return ConnParams(
                server=profile.server,
                database=profile.database,
                auth=profile.auth,
                username=profile.username,
                password=password,
                trust_cert=profile.trust_cert,
            )
        return None

    def _schedule_succeeded(self, path: str, count: int) -> None:
        changes: dict = {
            "last_run": datetime.now().isoformat(timespec="seconds"),
            "last_error": "",
            "retry_after": "",
        }
        if not self._schedule_manual and self._schedule_slot:
            if self._schedule_mode == "dates":
                job = next((item for item in load_jobs() if item.id == self._schedule_job_id), None)
                done = set(job.ran_dates if job else [])
                done.add(self._schedule_slot)
                changes["ran_dates"] = sorted(done)
            else:
                changes["last_slot"] = self._schedule_slot
        update_job(self._schedule_job_id, **changes)
        text = f"{count} строк → {path}"
        self._log(f"Расписание: {text}")
        self._tray_message("Выгрузка готова", text)

    def _schedule_failed(self, message: str) -> None:
        job = next((item for item in load_jobs() if item.id == self._schedule_job_id), None)
        name = job.name if job else "Задание"
        self._report_schedule(
            name,
            message,
            failed=True,
            job_id=self._schedule_job_id,
            slot=self._schedule_slot,
            manual=self._schedule_manual,
        )

    def _report_schedule(self, name: str, message: str, *, failed: bool, job_id: str, slot: str, manual: bool) -> None:
        if failed and not manual and job_id:
            update_job(
                job_id,
                last_error=message,
                retry_after=(datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds"),
            )
        text = f"«{name}»: {message}"
        self._log(f"Расписание {text}")
        self._tray_message("Выгрузка не выполнена" if failed else "Выгрузка", text)

    def _tray_message(self, title: str, text: str) -> None:
        if self._tray is not None and self._tray.isVisible():
            self._tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _stop_schedule_worker(self) -> None:
        worker = self._schedule_worker
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(2000)

    def _open_queries(self) -> None:
        dialog = SavedQueriesDialog(self)
        dialog.run_requested.connect(self._run_saved)
        dialog.exec()

    def _run_saved(self, sql: str) -> None:
        self.editor.setPlainText(sql)
        self._execute()

    def _export(self) -> None:
        if self._params is None or self._query_running:
            return
        tables = self.tree.checked_tables()
        if not tables:
            QMessageBox.information(self, "Выгрузка", "Отметьте таблицы в списке слева.")
            return
        dialog = ExportDialog(self._params, tables, self)
        dialog.exec()
