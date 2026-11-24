from PyQt6.QtCore import QDate, QSettings, QTime, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
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
)

from pasp.application.schedule import ScheduleJob, delete_job, describe_job, load_jobs, upsert_job
from pasp.infrastructure.profiles import load_profiles
from pasp.ui.theme import mono_font

_SETTINGS_DIR = "exportDir"


def remembered_folder() -> str:
    return str(QSettings("PASP", "BD_PASP").value(_SETTINGS_DIR, "") or "")


def remember_folder(path: str) -> None:
    if path.strip():
        QSettings("PASP", "BD_PASP").setValue(_SETTINGS_DIR, path.strip())


class ScheduleEditDialog(QDialog):
    def __init__(self, job: ScheduleJob | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Задание выгрузки")
        self.resize(640, 640)
        self.setMinimumSize(560, 520)
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
        self.sql_edit.setPlaceholderText("SELECT * FROM dbo.Table WHERE CreatedAt >= @from AND CreatedAt < @to")
        self.sql_edit.setPlainText(job.sql if job else "")
        self.sql_edit.setMinimumHeight(120)
        self.schema_edit = QLineEdit(job.target_schema if job else "dbo")
        self.table_edit = QLineEdit(job.target_table if job else "")
        self.folder_edit = QLineEdit(job.output_dir if job and job.output_dir else remembered_folder())
        self.folder_edit.setPlaceholderText("Папка, куда каждый раз складывать .sql")
        browse = QPushButton("Обзор")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._browse)
        self.identity_box = QCheckBox("Включать IDENTITY_INSERT")
        self.identity_box.setChecked(bool(job.identity) if job else False)
        self.enabled_box = QCheckBox("Задание включено")
        self.enabled_box.setChecked(True if job is None else job.enabled)

        self.monthly = QRadioButton("Каждый месяц")
        self.by_dates = QRadioButton("По указанным датам")
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

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow("Имя", self.name_edit)
        form.addRow("Подключение", self.profile_combo)
        form.addRow("Запрос", self.sql_edit)
        form.addRow("Схема", self.schema_edit)
        form.addRow("Таблица для INSERT", self.table_edit)
        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Папка", folder_row)
        form.addRow("", self.identity_box)
        form.addRow("", self.enabled_box)
        form.addRow("", self.monthly)
        form.addRow("Число месяца", self.day_spin)
        form.addRow("", self.by_dates)
        form.addRow("Время", self.time_edit)

        date_buttons = QHBoxLayout()
        date_buttons.setSpacing(8)
        date_buttons.addWidget(self.date_edit)
        date_buttons.addWidget(add_date)
        date_buttons.addWidget(remove_date)
        form.addRow("Даты", date_buttons)
        form.addRow("", self.date_list)

        hint = QLabel("Папка сохраняется в задании. Повторные запуски пишут новые файлы в неё же.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

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
        root.addLayout(form)
        root.addWidget(hint)
        root.addLayout(buttons)
        self._sync_mode()
        self._job: ScheduleJob | None = None

    def job(self) -> ScheduleJob | None:
        return self._job

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
        if not name or not sql or not table or not folder:
            QMessageBox.warning(self, "Задание", "Нужны имя, запрос, таблица и папка выгрузки.")
            return
        if self.profile_combo.currentData() is None:
            QMessageBox.warning(self, "Задание", "Сначала сохраните подключение к серверу.")
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
        )
        remember_folder(folder)
        self.accept()


class ScheduleDialog(QDialog):
    run_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Расписание")
        self.resize(720, 480)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._edit())
        add_button = QPushButton("Добавить")
        edit_button = QPushButton("Изменить")
        delete_button = QPushButton("Удалить")
        run_button = QPushButton("Выполнить сейчас")
        run_button.setObjectName("PrimaryButton")
        close_button = QPushButton("Закрыть")
        for button in (add_button, edit_button, delete_button, run_button, close_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        add_button.clicked.connect(self._add)
        edit_button.clicked.connect(self._edit)
        delete_button.clicked.connect(self._delete)
        run_button.clicked.connect(self._run)
        close_button.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(add_button)
        buttons.addWidget(edit_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        buttons.addWidget(run_button)
        hint = QLabel("У каждого задания своя папка. Она не спрашивается заново.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        title = QLabel("Задания")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addWidget(self.list, 1)
        root.addWidget(hint)
        root.addLayout(buttons)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for job in load_jobs():
            state = "вкл" if job.enabled else "выкл"
            item = QListWidgetItem(f"{job.name}  ·  {describe_job(job)}  ·  {job.output_dir}  ·  {state}")
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

    def _add(self) -> None:
        dialog = ScheduleEditDialog(parent=self)
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
