"""Import genotypes from the lab's FlyStockTable.csv.

Expected columns (header row, case- and whitespace-insensitive):
    No, Strain Name, Chromosome 1, Chromosome 2, Chromosom 3, Donor,
    Donor ID, Vials active, Wildtype, notes

"Chromosome 1" → chromosome_x  (fly genetics convention)
"Vials active" is informational and ignored.
Empty Strain Name falls back to f"strain_{No}".
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from ddb.models import Donor, Genotype


@dataclass
class ImportReport:
    genotypes_created: int = 0
    genotypes_skipped: int = 0  # already present (matched by name)
    donors_created: int = 0
    rows_failed: list[tuple[int, str]] = None  # (row_no, reason)

    def __post_init__(self) -> None:
        if self.rows_failed is None:
            self.rows_failed = []


def _norm_chrom(value: str | None) -> str | None:
    """Strip the "+ / +" CSV-escape artefacts; treat blanks as None."""
    if value is None:
        return None
    v = value.strip().strip('"').strip()
    return v or None


def _norm_str(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v or v == "-":
        return None
    return v


def _parse_bool(value: str | None) -> bool:
    return (value or "").strip().upper() == "TRUE"


def _normalise_headers(fieldnames: list[str]) -> dict[str, str]:
    """Map normalised header → original header so we can index dict rows."""
    out: dict[str, str] = {}
    for name in fieldnames:
        key = name.strip().lower().replace(" ", "_")
        # tolerate the typo in source CSV
        key = key.replace("chromosom_3", "chromosome_3")
        out[key] = name
    return out


def import_genotypes_csv(csv_path: Path, session: Session) -> ImportReport:
    report = ImportReport()
    donor_cache: dict[str, Donor] = {}

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        h = _normalise_headers(reader.fieldnames)

        required = {"no", "strain_name", "chromosome_1", "chromosome_2", "chromosome_3", "donor"}
        missing = required - h.keys()
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")

        for row in reader:
            row_no_raw = row[h["no"]].strip()
            try:
                row_no = int(row_no_raw)
            except ValueError:
                report.rows_failed.append((-1, f"non-integer 'No' value: {row_no_raw!r}"))
                continue

            name = _norm_str(row[h["strain_name"]]) or f"strain_{row_no}"

            existing = session.exec(select(Genotype).where(Genotype.name == name)).first()
            if existing is not None:
                report.genotypes_skipped += 1
                continue

            donor_name = _norm_str(row[h["donor"]])
            donor: Donor | None = None
            if donor_name:
                if donor_name in donor_cache:
                    donor = donor_cache[donor_name]
                else:
                    donor = session.exec(select(Donor).where(Donor.name == donor_name)).first()
                    if donor is None:
                        donor = Donor(name=donor_name)
                        session.add(donor)
                        session.flush()  # populate donor.id
                        report.donors_created += 1
                    donor_cache[donor_name] = donor

            geno = Genotype(
                name=name,
                chromosome_x=_norm_chrom(row[h["chromosome_1"]]),
                chromosome_2=_norm_chrom(row[h["chromosome_2"]]),
                chromosome_3=_norm_chrom(row[h["chromosome_3"]]),
                phenotype=None,
                notes=_norm_str(row.get(h.get("notes", ""), "")),
                is_wildtype=_parse_bool(row.get(h.get("wildtype", ""), "")),
                donor_strain_id=_norm_str(row.get(h.get("donor_id", ""), "")),
                donor_id=donor.id if donor else None,
            )
            session.add(geno)
            report.genotypes_created += 1

        session.commit()
    return report
