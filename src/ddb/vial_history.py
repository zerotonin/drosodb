# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — vial_history                                              ║
# ║  « every recorded event of one vial, in plain words »            ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Turns raw audit_event rows into readable trail entries: ids in  ║
# ║  payloads become print codes and names, actions become labels.   ║
# ╚══════════════════════════════════════════════════════════════════╝
"""Readable audit trail for a single vial.

The audit table stores ids inside JSON payloads (``new_vial_id``,
``genotype_id``, ``owner_id``...). Ids mean nothing to a biologist, so each
event is turned into a short label ("Flipped") plus a summary that names
the other vials, the genotype and the people involved.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from ddb.models import AuditEvent, Genotype, OrgUnit, User, Vial

Payload = dict[str, Any]

# ┌────────────────────────────────────────────────────────────┐
# │ Trail entries  « one row per audit event »                 │
# └────────────────────────────────────────────────────────────┘


@dataclass(frozen=True)
class TrailEntry:
    """One audit event of a vial, ready to display."""

    when: datetime
    actor: str | None
    action: str
    label: str
    summary: str

    CSV_HEADERS = ("when_utc", "actor", "action", "label", "summary")

    def as_csv_row(self) -> list[str]:
        return [
            self.when.isoformat(timespec="seconds"),
            self.actor or "",
            self.action,
            self.label,
            self.summary,
        ]


def vial_audit_trail(session: Session, vial_id: int) -> list[TrailEntry]:
    """All audit events recorded for a vial, oldest first."""
    names = _NameLookup(session)
    rows = session.exec(
        select(AuditEvent, User)
        .join(User, AuditEvent.actor_id == User.id, isouter=True)
        .where(AuditEvent.entity_type == "vial", AuditEvent.entity_id == vial_id)
        .order_by(AuditEvent.created_at, AuditEvent.id)
    ).all()
    entries = []
    for event, actor in rows:
        label, summary = describe_event(event.action, event.payload or {}, names)
        entries.append(
            TrailEntry(
                when=event.created_at,
                actor=actor.username if actor else None,
                action=event.action,
                label=label,
                summary=summary,
            )
        )
    return entries


def export_trail_csv(entries: list[TrailEntry], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(TrailEntry.CSV_HEADERS)
        writer.writerows(entry.as_csv_row() for entry in entries)
    return out_path


# ┌────────────────────────────────────────────────────────────┐
# │ Event descriptions  « action + payload → label + summary » │
# └────────────────────────────────────────────────────────────┘

DECOMMISSION_REASON_LABELS: dict[str, str] = {
    "flip": "Flipped",
    "multiply": "Multiplied",
    "flip_all_for_genotype": "Batch flipped",
}


def describe_event(action: str, payload: Payload, names: _NameLookup) -> tuple[str, str]:
    """Return ``(label, summary)`` for one audit event."""
    describer = _DESCRIBERS.get(action, _describe_unknown)
    return describer(action, payload, names)


def _describe_create(_action: str, p: Payload, names: _NameLookup) -> tuple[str, str]:
    parts = [f"Genotype {names.genotype(p.get('genotype_id'))}"]
    if p.get("owner_id") is not None:
        parts.append(f"owner {names.user(p['owner_id'])}")
    if p.get("org_unit_id") is not None:
        parts.append(f"unit {names.org_unit(p['org_unit_id'])}")
    return "Created", " · ".join(parts)


def _describe_flip_from(_action: str, p: Payload, names: _NameLookup) -> tuple[str, str]:
    parent = p.get("from_print_code") or names.vial(p.get("from_vial_id"))
    if p.get("siblings") is not None:
        siblings = ", ".join(p["siblings"]) or "none"
        return "Multiplied from", f"{parent} (siblings: {siblings})"
    if p.get("batch"):
        return "Batch flipped from", f"{parent}"
    return "Flipped from", f"{parent}"


def _describe_decommission(_action: str, p: Payload, names: _NameLookup) -> tuple[str, str]:
    reason = str(p.get("reason") or "")
    label = DECOMMISSION_REASON_LABELS.get(reason)
    if label is not None:
        codes = p.get("new_print_codes") or [
            names.vial(vid) for vid in (p.get("new_vial_ids") or [p.get("new_vial_id")])
        ]
        return f"{label} into", ", ".join(codes)
    return "Decommissioned", f"Reason: {reason}" if reason else "No reason given"


def _describe_reactivate(_action: str, p: Payload, _names: _NameLookup) -> tuple[str, str]:
    descendants = p.get("active_descendants") or []
    if descendants:
        return "Reactivated", "Active descendants at the time: " + ", ".join(descendants)
    return "Reactivated", "No active descendants at the time"


def _describe_unknown(action: str, p: Payload, _names: _NameLookup) -> tuple[str, str]:
    label = action.replace("_", " ").capitalize()
    return label, ", ".join(f"{k}: {v}" for k, v in sorted(p.items()))


_DESCRIBERS: dict[str, Callable[[str, Payload, _NameLookup], tuple[str, str]]] = {
    "create": _describe_create,
    "flip_from": _describe_flip_from,
    "decommission": _describe_decommission,
    "reactivate": _describe_reactivate,
}


# ┌────────────────────────────────────────────────────────────┐
# │ Name lookups  « ids → codes and names, cached »            │
# └────────────────────────────────────────────────────────────┘


class _NameLookup:
    """Resolve payload ids to display names; unknown ids render as ``#id``."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._cache: dict[tuple[type, int], str] = {}

    def vial(self, vial_id: int | None) -> str:
        return self._name(Vial, vial_id, lambda v: v.print_code)

    def genotype(self, genotype_id: int | None) -> str:
        return self._name(Genotype, genotype_id, lambda g: g.name)

    def user(self, user_id: int | None) -> str:
        return self._name(User, user_id, lambda u: u.username)

    def org_unit(self, unit_id: int | None) -> str:
        return self._name(OrgUnit, unit_id, lambda o: o.name)

    def _name(self, model: type, row_id: int | None, render: Callable[[Any], str]) -> str:
        if row_id is None:
            return "—"
        key = (model, int(row_id))
        if key not in self._cache:
            row = self._session.get(model, row_id)
            self._cache[key] = render(row) if row is not None else f"#{row_id}"
        return self._cache[key]
