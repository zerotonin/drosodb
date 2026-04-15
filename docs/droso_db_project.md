# Drosophila Vial Tracking System (DDB)

## Overview
A Python-based system to manage Drosophila vials, including:
- QR-code based identification
- Automated label printing
- Full lineage tracking
- SQL-based database backend
- Qt GUI for lab use

---

## Core Components

### Database
Recommended: PostgreSQL

Key tables:
- Users
- Organisational Units
- Donors
- Genotypes
- Genotype Status
- Vials
- Vial Status
- Audit Log

Features:
- Foreign key relationships
- Partial unique index for active vial print codes
- Full-text genotype search

---

### Hardware
- Device: Star Lite 5 (Ubuntu tablet with camera)
- Printer: Brother QL-820NWB
- Labels: DK-11204 (17x54 mm)

---

### QR Code System
Payload format:
`ddb:1:vial:<vial_id>?pc=<PRINT>&db=<DBID>`

- Micro QR preferred
- Must fit ~13–14 mm square
- Fallback: human-readable print code

---

### Print Code
- Base32 (Crockford alphabet)
- 4–5 characters
- Optional checksum

---

### Software Stack
- Backend: FastAPI + SQLModel
- GUI: PySide6 (Qt)
- QR: Segno
- Camera: OpenCV
- Printing: brother_ql

---

## Workflows

### Import / Initialise
- Create genotype
- Create vial
- Print label

### Copy Vial
- Duplicate vial
- Link parent
- New print code

### Cross Vial
- Two parents
- New genotype required

### Flip Vial
- Create new vial
- Decommission old vial

### Group Flip
- Batch version of Flip
- Optional strict pairing mode

### Status Change
- Modify vial/genotype status

### Decommission
- End-of-life for vial

---

## Reports
- Vial details
- All vials (active/all)
- By user
- By organisational unit
- By genotype
- By gene (text search)
- By donor
- By status

---

## Key Design Decisions
- PostgreSQL over SQLite for concurrency
- QR codes over barcodes for density
- Audit logging for all critical actions
- Separation of genotype and vial states

---

## Future Development
- Mobile apps (Android/iOS)
- Cloud deployment
- Automated backups
- Advanced lineage visualisation

---

## Milestones
1. Database + API
2. Label printing
3. QR scanning
4. Core workflows
5. GUI
6. Deployment

---

## Notes
- Always confirm label printing via re-scan
- Maintain strict lineage tracking
- Prioritise usability in lab conditions

