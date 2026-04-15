# Drosophila Vial Tracking System (DDB) – Extended Spec

## Overview
A Python-based system to manage Drosophila vials with:
- QR-code identification
- Automated label printing
- Full lineage tracking
- SQL backend (PostgreSQL recommended)
- Qt GUI for lab workflows

---

## Project Parameters

### Design Goals
- Fast vial handling during real lab work
- Error-resistant (QR + fallback code)
- Full lineage traceability
- Multi-user concurrent access
- Minimal friction UI (scan-driven)

### Constraints
- Label size: 17x54 mm (DK-11204)
- QR code must be readable at ~13–14 mm
- Works offline temporarily (optional local cache)
- Runs on Linux tablet (Star Lite 5)

### Scale Assumptions
- Up to ~10 million concurrent vial codes (4-char space)
- Multiple users simultaneously
- Long-term database growth (years of lineage)

---

## Hardware

- Device: Star Lite 5 (Ubuntu, camera)
- Printer: Brother QL-820NWB
- Labels: DK-11204 (17x54 mm)

---

## Database Design

Core tables:
- User
- Organisational Unit
- Donor
- Genotype
- GenotypeStatus
- Vial
- VialStatus
- AuditEvent

Key features:
- Foreign keys for lineage
- Many-to-many for statuses
- Partial unique index on active print_code
- Trigram index for genotype search

---

## QR Code System

Payload:
`ddb:1:vial:<vial_id>?pc=<PRINT>&db=<DBID>`

Design:
- Micro QR preferred
- Error correction: M
- Keep payload minimal

Fallback:
- Human-readable print code (Base32)

---

## Software Stack

- Backend: FastAPI + SQLModel
- GUI: PySide6 (Qt)
- QR: Segno
- Camera: OpenCV
- Printing: brother_ql
- Migrations: Alembic
- Packaging: Poetry / Conda

---

## Workflows

### Import / Initialise
- Create genotype
- Create vial
- Print + confirm

### Copy
- Duplicate vial
- Preserve genotype
- Link parent

### Cross
- Two parents
- New genotype required

### Flip
- Replace vial
- Decommission old

### Group Flip
- Batch operation
- Optional strict validation

### Status Change
- Modify genotype or vial status

### Decommission
- End-of-life marking

---

## Reports

- Vial details
- All vials
- By user / unit
- By genotype
- Gene search
- By donor
- By status

---

## Publications & Prior Art

### Drosophila-Specific Systems
- FlyORF + SLIMS workflow (barcode scanning + automated label printing)
- LabCollector Fly Stock Manager (flip workflows, tube tracking)
- DroST database (barcode-based stock tracking)

### General LIMS
- MetaLIMS (QR support)
- OpenLabFramework (barcode workflows)
- LabKey Sample Manager (barcode + sample tracking)

### Technical References
- QR Code standard: ISO/IEC 18004
- Micro QR (DENSO Wave)
- OpenCV QRCodeDetector
- ZXing / zxing-cpp

---

## Key Design Decisions

- PostgreSQL for concurrency and indexing
- QR over barcode for density
- Scan-first UX design
- Audit log for all critical actions
- Separation of genotype vs vial state

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| QR unreadable | fallback print code |
| User errors | scan-confirm workflow |
| DB conflicts | transactions + constraints |
| Label misprint | re-scan confirmation |

---

## Milestones

1. DB + API
2. Label rendering + printing
3. QR scanning
4. Core workflows
5. GUI
6. Deployment & testing

---

## Future Development

- Mobile apps (Android/iOS)
- Cloud sync
- Lineage visualisation graphs
- Integration with ordering systems

---

## Notes

- Always confirm prints via scan
- Prioritise speed + clarity for lab use
- Design for tired, distracted humans

