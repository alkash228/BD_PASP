from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from pasp.application.workers import ConnectWorker
from pasp.infrastructure.mssql import ConnParams, pick_driver
from pasp.infrastructure.profiles import Profile, delete_profile, load_profiles, upsert_profile
from pasp.infrastructure.secrets import delete_password, load_password, save_password


class ConnectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ConnectDialog")
        self.setWindowTitle("Подключение к SQL Server")
        self.setModal(True)
        self.resize(720, 520)
        self.setMinimumSize(640, 460)

        self._profiles = load_profiles()
        self._profile_id: str | None = None
        self._waiting = False
        self._worker: ConnectWorker | None = None
        self._params: ConnParams | None = None
        self.password_warning = ""

        self.profile_list = QListWidget()
        self.profile_list.setAccessibleName("Сохранённые профили")
        self.profile_list.currentRowChanged.connect(self._load_selected)
        self.profile_list.itemDoubleClicked.connect(lambda _item: self._connect())

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Рабочий сервер")
        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText(r"localhost или host\instance")
        self.database_edit = QLineEdit()
        self.database_edit.setPlaceholderText("необязательно")
        self.auth_combo = QComboBox()
        self.auth_combo.addItem("Проверка подлинности Windows", "windows")
        self.auth_combo.addItem("Проверка подлинности SQL Server", "sql")
        self.auth_combo.currentIndexChanged.connect(self._sync_auth)
        self.user_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_box = QCheckBox("Запомнить пароль")
        self.trust_box = QCheckBox("Доверять сертификату сервера")
        self.trust_box.setChecked(True)
        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorText")
        self.error_label.setWordWrap(True)
        self.driver_label = QLabel("")
        self.driver_label.setObjectName("Muted")
        self.driver_label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.hide()

        for label, widget in (
            ("Имя профиля", self.name_edit),
            ("Сервер", self.server_edit),
            ("База данных", self.database_edit),
            ("Проверка подлинности", self.auth_combo),
            ("Логин", self.user_edit),
            ("Пароль", self.password_edit),
        ):
            if isinstance(widget, QLineEdit):
                widget.setAccessibleName(label)

        form = QFormLayout()
        form.setSpacing(8)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Имя профиля", self.name_edit)
        form.addRow("Сервер", self.server_edit)
        form.addRow("База данных", self.database_edit)
        form.addRow("Проверка подлинности", self.auth_combo)
        form.addRow("Логин", self.user_edit)
        form.addRow("Пароль", self.password_edit)
        form.addRow("", self.remember_box)
        form.addRow("", self.trust_box)

        self.connect_button = QPushButton("Подключить")
        self.connect_button.setObjectName("PrimaryButton")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self._connect)
        self.connect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        new_button = QPushButton("Новый")
        new_button.clicked.connect(self._new_profile)
        new_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button = QPushButton("Удалить")
        delete_button.clicked.connect(self._delete_profile)
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_button = QPushButton("Отмена")
        cancel_button.clicked.connect(self.reject)
        cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(new_button)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.connect_button)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.setContentsMargins(16, 0, 0, 0)
        right.addLayout(form)
        right.addWidget(self.driver_label)
        right.addWidget(self.error_label)
        right.addWidget(self.progress)
        right.addStretch(1)
        right.addLayout(buttons)

        left_title = QLabel("Профили")
        left_title.setObjectName("SectionTitle")
        left = QVBoxLayout()
        left.setSpacing(8)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(left_title)
        left.addWidget(self.profile_list, 1)

        body = QHBoxLayout()
        body.setSpacing(8)
        body.addLayout(left, 2)
        body.addLayout(right, 3)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        title = QLabel("Подключение")
        title.setObjectName("SectionTitle")
        root.addWidget(title)
        root.addLayout(body, 1)

        self._fill_profiles()
        self._sync_auth()
        self._show_driver()
        self.server_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _show_driver(self) -> None:
        try:
            driver = pick_driver()
        except RuntimeError as exc:
            self.driver_label.setText(str(exc))
            self.connect_button.setEnabled(False)
            return
        if driver == "SQL Server":
            self.driver_label.setText(
                "Драйвер: SQL Server. Для шифрования установите ODBC Driver 18 for SQL Server."
            )
        else:
            self.driver_label.setText(f"Драйвер: {driver}")

    def params(self) -> ConnParams | None:
        return self._params

    def _fill_profiles(self) -> None:
        self.profile_list.clear()
        for profile in self._profiles:
            item = QListWidgetItem(profile.name or profile.server)
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_list.addItem(item)

    def _selected_profile(self) -> Profile | None:
        item = self.profile_list.currentItem()
        if item is None:
            return None
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        for profile in self._profiles:
            if profile.id == profile_id:
                return profile
        return None

    def _load_selected(self, _row: int) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        self._profile_id = profile.id
        self.name_edit.setText(profile.name)
        self.server_edit.setText(profile.server)
        self.database_edit.setText(profile.database)
        index = self.auth_combo.findData(profile.auth)
        self.auth_combo.setCurrentIndex(max(0, index))
        self.user_edit.setText(profile.username)
        self.trust_box.setChecked(profile.trust_cert)
        password = load_password(profile.id) if profile.auth == "sql" else ""
        self.password_edit.setText(password)
        self.remember_box.setChecked(bool(password))
        self._sync_auth()

    def _new_profile(self) -> None:
        self.profile_list.clearSelection()
        self._profile_id = None
        self.name_edit.clear()
        self.server_edit.clear()
        self.database_edit.clear()
        self.user_edit.clear()
        self.password_edit.clear()
        self.remember_box.setChecked(False)
        self.trust_box.setChecked(True)
        self.auth_combo.setCurrentIndex(0)
        self.error_label.clear()
        self.server_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _delete_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        answer = QMessageBox.question(
            self,
            "Удалить профиль",
            f"Удалить «{profile.name}» и сохранённый пароль?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        delete_profile(profile.id)
        delete_password(profile.id)
        self._profiles = load_profiles()
        self._fill_profiles()
        self._new_profile()

    def _sync_auth(self) -> None:
        sql_auth = self.auth_combo.currentData() == "sql"
        self.user_edit.setEnabled(sql_auth)
        self.password_edit.setEnabled(sql_auth)
        self.remember_box.setEnabled(sql_auth)

    def _set_waiting(self, waiting: bool) -> None:
        self._waiting = waiting
        self.connect_button.setEnabled(not waiting)
        self.progress.setVisible(waiting)
        if waiting:
            self.progress.setRange(0, 0)
            self.error_label.setText("Подключение…")
        else:
            self.progress.setRange(0, 1)
            self.progress.hide()

    def _connect(self) -> None:
        if self._waiting:
            return
        server = self.server_edit.text().strip()
        if not server:
            self.error_label.setText("Укажите сервер.")
            self.server_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        auth = str(self.auth_combo.currentData())
        username = self.user_edit.text().strip()
        if auth == "sql" and not username:
            self.error_label.setText("Укажите логин SQL Server.")
            self.user_edit.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._params = ConnParams(
            server=server,
            database=self.database_edit.text().strip(),
            auth=auth,
            username=username,
            password=self.password_edit.text(),
            trust_cert=self.trust_box.isChecked(),
        )
        self._set_waiting(True)
        worker = ConnectWorker(self._params)
        worker.succeeded.connect(self._on_connected)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_connected(self, database: str) -> None:
        if not self._waiting or self._params is None:
            return
        self._set_waiting(False)
        profile = self._build_profile(database)
        try:
            upsert_profile(profile)
            if profile.auth == "sql" and self.remember_box.isChecked() and self._params.password:
                save_password(profile.id, self._params.password)
            else:
                delete_password(profile.id)
        except Exception as exc:
            self.password_warning = f"Профиль сохранён не полностью: {exc}"
        if database and not self._params.database:
            self._params = ConnParams(
                server=self._params.server,
                database=database,
                auth=self._params.auth,
                username=self._params.username,
                password=self._params.password,
                trust_cert=self._params.trust_cert,
            )
        self.accept()

    def _build_profile(self, database: str) -> Profile:
        name = self.name_edit.text().strip() or self._params.server
        return Profile(
            id=self._profile_id or Profile.new_id(),
            name=name,
            server=self._params.server,
            database=self.database_edit.text().strip() or database,
            auth=self._params.auth,
            username=self._params.username if self._params.auth == "sql" else "",
            trust_cert=self._params.trust_cert,
        )

    def _on_failed(self, message: str) -> None:
        if not self._waiting:
            return
        self._set_waiting(False)
        self.error_label.setText(message)

    def reject(self) -> None:
        self._waiting = False
        super().reject()
