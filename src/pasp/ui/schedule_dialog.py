from PyQt6.QtCore import QDate, QSettings, QSize, QTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from datetime import date
from pathlib import Path

from pasp.application.schedule import (
    ScheduleJob,
    delete_job,
    describe_job,
    load_jobs,
    scheduled_export_name,
    upsert_job,
)
from pasp.infrastructure.profiles import load_profiles
from pasp.ui.theme import mono_font

_SETTINGS_DIR = "exportDir"


def remembered_folder() -> str:
    return str(QSettings("PASP", "BD_PASP").value(_SETTINGS_DIR, "") or "")


def remember_folder(path: str) -> None:
    if path.strip():
        QSettings("PASP", "BD_PASP").setValue(_SETTINGS_DIR, path.strip())


class ScheduleEditDialog(QDialog):
    def __init__(self, job: ScheduleJob | None = None, parent=None, action: str = "export"):
        super().__init__(parent)
        self.setWindowTitle("Задание")
        self.resize(640, 680)
        self.setMinimumSize(560, 560)
        self._job_id = job.id if job else ScheduleJob.new_id()
        self._ran_dates = list(job.ran_dates) if job else []
        self._last_slot = job.last_slot if job else ""
        self._last_run = job.last_run if job else ""
        self._last_error = job.last_error if job else ""
        self._retry_after = job.retry_after if job else ""

        self.name_edit = QLineEdit(job.name if job else "")
        self.profile_combo = QComboBox()
        for profile in load_profiles():
            self.profile_combo.addItem(profile.name or profile.server, profile.id)
        if job and job.profile_id:
            index = self.profile_combo.findData(job.profile_id)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        self.sql_edit = QPlainTextEdit()
        self.sql_edit.setFont(mono_font())
        self.sql_edit.setPlaceholderText("SELECT * FROM dbo.Таблица")
        self.sql_edit.setPlainText(job.sql if job else "")
        self.sql_edit.setMinimumHeight(120)
        self.schema_edit = QLineEdit(job.target_schema if job else "dbo")
        self.table_edit = QLineEdit(job.target_table if job else "")
        self.table_edit.setPlaceholderText("Как таблица будет называться в файле")
        self.folder_edit = QLineEdit(job.output_dir if job and job.output_dir else remembered_folder())
        self.folder_edit.setPlaceholderText("Папка, куда класть файл")
        browse = QPushButton("Обзор")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        self.identity_box = QCheckBox("В таблице есть столбец-счётчик IDENTITY")
        self.identity_box.setChecked(bool(job.identity) if job else False)
        self.enabled_box = QCheckBox("Выполнять это задание")
        self.enabled_box.setChecked(True if job is None else job.enabled)
        self.export_action = QRadioButton("Выгрузить запрос в файл")
        self.load_action = QRadioButton("Загрузить файл на сервер")
        if (job and job.action == "load") or (job is None and action == "load"):
            self.load_action.setChecked(True)
        else:
            self.export_action.setChecked(True)
        self.export_action.toggled.connect(self._sync_action)
        self.file_edit = QLineEdit(job.source_file if job else "")
        self.file_edit.setPlaceholderText("script.sql")
        file_browse = QPushButton("Указать файл")
        file_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        file_browse.clicked.connect(self._browse_script)

        self.monthly = QRadioButton("Каждый месяц в один и тот же день")
        self.by_dates = QRadioButton("Только в указанные даты")
        if job and job.mode == "dates":
            self.by_dates.setChecked(True)
        else:
            self.monthly.setChecked(True)
        self.monthly.toggled.connect(self._sync_mode)
        self.day_spin = QSpinBox()
        self.day_spin.setRange(1, 31)
        self.day_spin.setValue(job.day if job else 1)
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setTime(QTime(job.hour if job else 12, job.minute if job else 0))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        self.date_list = QListWidget()
        if job:
            for value in job.dates:
                self.date_list.addItem(value)
        add_date = QPushButton("Добавить дату")
        remove_date = QPushButton("Убрать")
        add_date.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_date.setCursor(Qt.CursorShape.PointingHandCursor)
        add_date.clicked.connect(self._add_date)
        remove_date.clicked.connect(self._remove_date)

        what = QFormLayout()
        what.setSpacing(8)
        what.addRow("Как назвать задание", self.name_edit)
        what.addRow("Какой учёткой входить", self.profile_combo)
        what.addRow("Что сделать", self.export_action)
        what.addRow("", self.load_action)
        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(file_browse)
        file_host = QWidget()
        file_host.setLayout(file_row)
        self._file_host = file_host
        self._file_row = QLabel("Имя файла в папке")
        what.addRow(self._file_row, file_host)
        what.addRow("Какой запрос выполнить", self.sql_edit)
        what.addRow("Схема в файле", self.schema_edit)
        what.addRow("Таблица в файле", self.table_edit)
        what.addRow("", self.identity_box)
        self._what = what
        self.what_box = QGroupBox("Что выгрузить")
        self.what_box.setLayout(what)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        where = QFormLayout()
        where.setSpacing(8)
        where.addRow("Папка", folder_row)
        self.preview = QLabel()
        self.preview.setObjectName("Muted")
        self.preview.setWordWrap(True)
        where.addRow(self.preview)
        self.where_box = QGroupBox("Куда положить файл")
        self.where_box.setLayout(where)

        when = QFormLayout()
        when.setSpacing(8)
        when.addRow("", self.enabled_box)
        when.addRow("", self.monthly)
        when.addRow("День месяца", self.day_spin)
        when.addRow("", self.by_dates)
        when.addRow("Во сколько", self.time_edit)
        date_buttons = QHBoxLayout()
        date_buttons.setSpacing(8)
        date_buttons.addWidget(self.date_edit)
        date_buttons.addWidget(add_date)
        date_buttons.addWidget(remove_date)
        when.addRow("Даты", date_buttons)
        when.addRow("", self.date_list)
        when_box = QGroupBox("Когда это делать")
        when_box.setLayout(when)

        save_button = QPushButton("Сохранить")
        save_button.setObjectName("PrimaryButton")
        save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        save_button.clicked.connect(self._save)
        cancel = QPushButton("Отмена")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save_button)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        root.addWidget(self.what_box)
        root.addWidget(self.where_box)
        root.addWidget(when_box)
        root.addLayout(buttons)
        self.schema_edit.textChanged.connect(self._refresh_preview)
        self.table_edit.textChanged.connect(self._refresh_preview)
        self.folder_edit.textChanged.connect(self._refresh_preview)
        self.day_spin.valueChanged.connect(self._refresh_preview)
        self.file_edit.textChanged.connect(self._refresh_preview)
        self._sync_mode()
        self._sync_action()
        self._job: ScheduleJob | None = None

    def job(self) -> ScheduleJob | None:
        return self._job

    def _browse_script(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            "Файл для загрузки на сервер",
            self.folder_edit.text(),
            "SQL (*.sql);;Все файлы (*.*)",
        )
        if not path:
            return
        file_path = Path(path)
        self.file_edit.setText(file_path.name)
        self.folder_edit.setText(str(file_path.parent))
        remember_folder(str(file_path.parent))

    def _sync_action(self) -> None:
        load = self.load_action.isChecked()
        for widget in (self.sql_edit, self.identity_box):
            self._show_field(widget, not load)
        for widget in (self.schema_edit, self.table_edit):
            self._show_field(widget, True)
        schema_label = self._what.labelForField(self.schema_edit)
        table_label = self._what.labelForField(self.table_edit)
        if schema_label is not None:
            schema_label.setText("Схема на моём сервере" if load else "Схема в файле")
        if table_label is not None:
            table_label.setText("Таблица на моём сервере" if load else "Таблица в файле")
        self._file_host.setVisible(load)
        self._file_row.setVisible(load)
        self.what_box.setTitle("Что загрузить" if load else "Что выгрузить")
        self.where_box.setTitle("Где лежит файл" if load else "Куда положить файл")
        self.setWindowTitle("Загрузка файла на сервер" if load else "Выгрузка запроса в файл")
        self._refresh_preview()

    def _show_field(self, widget, visible: bool) -> None:
        widget.setVisible(visible)
        label = self._what.labelForField(widget)
        if label is not None:
            label.setVisible(visible)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка выгрузки", self.folder_edit.text() or remembered_folder())
        if folder:
            self.folder_edit.setText(folder)
            remember_folder(folder)

    def _sync_mode(self) -> None:
        monthly = self.monthly.isChecked()
        self.day_spin.setEnabled(monthly)
        self.date_edit.setEnabled(not monthly)
        self.date_list.setEnabled(not monthly)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        schema = self.schema_edit.text().strip() or "dbo"
        table = self.table_edit.text().strip() or "Таблица"
        today = date.today()
        if self.monthly.isChecked():
            slot = date(today.year, today.month, min(self.day_spin.value(), 28)).isoformat()
            when = f"каждый месяц, {self.day_spin.value()}-го числа"
        else:
            slot = today.isoformat()
            when = "в каждую выбранную дату"
        folder = self.folder_edit.text().strip() or "выбранной папке"
        if self.load_action.isChecked():
            filename = self.file_edit.text().strip() or "файл.sql"
            schema = self.schema_edit.text().strip() or "dbo"
            table = self.table_edit.text().strip() or "Таблица"
            self.preview.setText(
                f"{when.capitalize()} программа проверит «{folder}». "
                f"Если там есть {filename}, вставит его строки в [{schema}].[{table}] на вашем сервере. "
                "Если файла нет, подождёт и проверит снова."
            )
            return
        name = scheduled_export_name(schema, table, slot)
        self.preview.setText(
            f"{when.capitalize()} появится один файл {name} в «{folder}». "
            "Если он уже лежит там, запрос не запустится. "
            "Чтобы выгрузить заново, удалите этот файл."
        )

    def _add_date(self) -> None:
        value = self.date_edit.date().toString("yyyy-MM-dd")
        existing = {self.date_list.item(index).text() for index in range(self.date_list.count())}
        if value not in existing:
            self.date_list.addItem(value)
            self.date_list.sortItems()

    def _remove_date(self) -> None:
        row = self.date_list.currentRow()
        if row >= 0:
            self.date_list.takeItem(row)

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        sql = self.sql_edit.toPlainText().strip()
        schema = self.schema_edit.text().strip() or "dbo"
        table = self.table_edit.text().strip()
        folder = self.folder_edit.text().strip()
        load = self.load_action.isChecked()
        source = self.file_edit.text().strip()
        if load:
            if not name or not folder or not source or not table:
                QMessageBox.warning(self, "Задание", "Заполните имя задания, имя файла, папку и таблицу на вашем сервере.")
                return
        elif not name or not sql or not table or not folder:
            QMessageBox.warning(self, "Задание", "Заполните имя задания, запрос, имя таблицы и папку.")
            return
        if self.profile_combo.currentData() is None:
            QMessageBox.warning(self, "Задание", "Сначала подключитесь к серверу и сохраните учётку. Тогда она появится в этом списке.")
            return
        dates = [self.date_list.item(index).text() for index in range(self.date_list.count())]
        if self.by_dates.isChecked() and not dates:
            QMessageBox.warning(self, "Задание", "Добавьте хотя бы одну дату.")
            return
        clock = self.time_edit.time()
        self._job = ScheduleJob(
            id=self._job_id,
            name=name,
            enabled=self.enabled_box.isChecked(),
            profile_id=str(self.profile_combo.currentData()),
            sql=self.sql_edit.toPlainText(),
            target_schema=schema,
            target_table=table,
            output_dir=folder,
            mode="dates" if self.by_dates.isChecked() else "monthly",
            day=self.day_spin.value(),
            hour=clock.hour(),
            minute=clock.minute(),
            dates=dates,
            identity=self.identity_box.isChecked(),
            last_slot=self._last_slot,
            ran_dates=self._ran_dates,
            last_run=self._last_run,
            last_error=self._last_error,
            retry_after=self._retry_after,
            action="load" if load else "export",
            source_file=source,
        )
        remember_folder(folder)
        self.accept()


class ScheduleDialog(QDialog):
    run_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Расписание")
        self.resize(760, 520)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        add_export = QPushButton("Выгрузка в файл")
        add_load = QPushButton("Загрузка на сервер")
        add_load.setObjectName("PrimaryButton")
        edit_button = QPushButton("Изменить")
        delete_button = QPushButton("Удалить")
        run_button = QPushButton("Проверить сейчас")
        run_button.setToolTip("Выгрузка создаст файл, если его ещё нет. Загрузка вставит строки из файла в таблицу на сервере, если файл уже лежит в папке.")
        close_button = QPushButton("Закрыть")
        for button in (add_export, add_load, edit_button, delete_button, run_button, close_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_export.clicked.connect(lambda: self._add("export"))
        add_load.clicked.connect(lambda: self._add("load"))
        edit_button.clicked.connect(self._edit)
        delete_button.clicked.connect(self._delete)
        run_button.clicked.connect(self._run)
        close_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(add_export)
        buttons.addWidget(add_load)
        buttons.addWidget(edit_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(run_button)
        hint = QLabel("«Выгрузка в файл» сохраняет результат запроса. «Загрузка на сервер» ждёт файл выгрузки в папке и вставляет его строки в вашу таблицу.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        title = QLabel("Задания")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addWidget(hint)
        root.addWidget(self.list, 1)
        root.addLayout(buttons)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for job in load_jobs():
            state = "включено" if job.enabled else "выключено"
            item = QListWidgetItem(f"{job.name} — {state}\n{describe_job(job)}\nПапка: {job.output_dir}")
            item.setSizeHint(QSize(0, 72))
            item.setData(Qt.ItemDataRole.UserRole, job.id)
            self.list.addItem(item)

    def _selected_id(self) -> str | None:
        item = self.list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole))

    def _job(self, job_id: str) -> ScheduleJob | None:
        for job in load_jobs():
            if job.id == job_id:
                return job
        return None

    def _add(self, action: str = "export") -> None:
        dialog = ScheduleEditDialog(parent=self, action=action)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.job() is not None:
            upsert_job(dialog.job())
            self._reload()

    def _edit(self) -> None:
        job_id = self._selected_id()
        if job_id is None:
            return
        dialog = ScheduleEditDialog(self._job(job_id), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.job() is not None:
            upsert_job(dialog.job())
            self._reload()

    def _delete(self) -> None:
        job_id = self._selected_id()
        if job_id is None:
            return
        if QMessageBox.question(self, "Расписание", "Удалить задание?") != QMessageBox.StandardButton.Yes:
            return
        delete_job(job_id)
        self._reload()

    def _run(self) -> None:
        job_id = self._selected_id()
        if job_id is not None:
            self.run_requested.emit(job_id)
            self.accept()
