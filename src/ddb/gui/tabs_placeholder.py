"""Placeholder tabs — filled in over the next few slices."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


def _placeholder(title: str, body: str) -> QWidget:
    w = QWidget()
    layout = QVBoxLayout(w)
    heading = QLabel(f"<h2>{title}</h2>")
    text = QLabel(body)
    text.setWordWrap(True)
    for x in (heading, text):
        x.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(x)
    layout.addStretch()
    return w


def build_reports_tab() -> QWidget:
    return _placeholder(
        "Reports",
        "Coming soon — filter form + results table backed by "
        "<code>ddb.reports.search_vials</code>.",
    )


def build_genotypes_tab() -> QWidget:
    return _placeholder(
        "Genotypes",
        "Coming soon — table of known strains with <i>create vial from this genotype</i> action.",
    )


def build_settings_tab() -> QWidget:
    return _placeholder(
        "Settings",
        "Coming soon — camera role assignment + database path configuration.",
    )
