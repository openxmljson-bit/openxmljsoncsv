"""License dialog (PySide6): email+OTP or license-key, read-only status.

Network runs on a background thread so the UI never freezes. This module is
self-contained and is NOT wired into app startup — call ``ensure_licensed()``
or open ``LicenseDialog`` explicitly.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from openxmljson.licensing import cache
from openxmljson.licensing.client import Entitlement, LicenseClient, LicenseError
from openxmljson.licensing.config import ApiConfig


class _Signals(QObject):
    ok = Signal(object)      # result (Entitlement or None)
    err = Signal(str)


class _Task(QRunnable):
    """Run a blocking callable off the UI thread."""

    def __init__(self, fn: Callable):
        super().__init__()
        self.setAutoDelete(False)     # keep alive until signals deliver
        self._fn = fn
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.ok.emit(self._fn())
        except LicenseError as exc:
            self.signals.err.emit(str(exc))
        except Exception as exc:      # noqa: BLE001 — surface unexpected errors
            self.signals.err.emit(f"Unexpected error: {exc}")


class LicenseDialog(QDialog):
    def __init__(self, cfg: Optional[ApiConfig] = None, parent=None):
        super().__init__(parent)
        self.cfg = cfg or ApiConfig.from_env()
        self.client = LicenseClient(self.cfg)
        self.entitlement: Optional[Entitlement] = None
        self._pool = QThreadPool.globalInstance()
        self._tasks: list = []        # keep refs (autoDelete off)
        self.setWindowTitle("OPENXMLJSON — Activate")
        self.setMinimumWidth(420)
        self._build()

    # -- UI --------------------------------------------------------------------
    def _build(self):
        layout = QVBoxLayout(self)
        intro = QLabel("Enter the license key and the email you used to "
                       "purchase. Your key was emailed to you after checkout.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 8, 0, 8)
        self.key_email = QLineEdit()
        self.key_email.setPlaceholderText("you@example.com")
        v.addWidget(QLabel("Email"))
        v.addWidget(self.key_email)
        self.key_value = QLineEdit()
        self.key_value.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX")
        v.addWidget(QLabel("License key"))
        v.addWidget(self.key_value)
        self.key_verify = QPushButton("Verify")
        self.key_verify.clicked.connect(self._verify_key)
        self.key_value.returnPressed.connect(self._verify_key)
        v.addWidget(self.key_verify)
        layout.addWidget(form)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.buy_btn = QPushButton("Buy / Manage subscription")
        self.buy_btn.clicked.connect(self._open_store)
        row.addWidget(self.buy_btn)
        row.addStretch(1)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.reject)
        row.addWidget(self.close_btn)
        layout.addLayout(row)

    # -- actions ---------------------------------------------------------------
    def _run(self, fn, on_ok):
        self._set_busy(True)
        task = _Task(fn)
        self._tasks.append(task)

        def done(result):
            self._set_busy(False)
            on_ok(result)

        def fail(msg):
            self._set_busy(False)
            self.status.setText(msg)
            QMessageBox.warning(self, "OPENXMLJSON", msg)

        task.signals.ok.connect(done)
        task.signals.err.connect(fail)
        self._pool.start(task)

    def _set_busy(self, busy: bool):
        self.key_verify.setEnabled(not busy)
        self.setCursor(Qt.CursorShape.WaitCursor if busy
                       else Qt.CursorShape.ArrowCursor)

    def _verify_key(self):
        email, key = self.key_email.text(), self.key_value.text()
        self._run(lambda: self.client.verify_key(email, key),
                  self._on_entitlement)

    def _on_entitlement(self, ent: Entitlement):
        self.entitlement = ent
        if ent.valid:
            cache.save(self.cfg, ent)
            exp = f" · expires {ent.expires_at}" if ent.expires_at else ""
            self.status.setText(
                f"✓ Active — {ent.tier or 'Licensed'}{exp}")
            QMessageBox.information(self, "OPENXMLJSON",
                                    "Your subscription is active. Thank you!")
            self.accept()
        else:
            self.status.setText(
                ent.reason or "No active subscription found for this account.")
            QMessageBox.information(
                self, "OPENXMLJSON",
                "No active subscription found. Use “Buy / Manage "
                "subscription” to get started.")

    def _open_store(self):
        QDesktopServices.openUrl(QUrl(self.cfg.store_url))


def ensure_licensed(parent=None, cfg: Optional[ApiConfig] = None,
                    force: bool = False) -> Optional[Entitlement]:
    """Return a valid cached entitlement, else open the dialog. Returns the
    Entitlement if activated, or None if the user cancelled/failed. Callers
    decide whether to soft- or hard-gate on the result."""
    cfg = cfg or ApiConfig.from_env()
    if not force:
        cached = cache.load(cfg)
        if cached is not None:
            return cached
    dlg = LicenseDialog(cfg, parent)
    dlg.exec()
    return dlg.entitlement if (dlg.entitlement and dlg.entitlement.valid) \
        else None
