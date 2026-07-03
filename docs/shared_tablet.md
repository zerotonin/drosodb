# Shared-tablet install

A step-by-step guide to putting DDB on a Linux tablet that several
biologists share, with each biologist logging in under their own OS
account. This is the model used at Otago on a wall-mounted Wacom-One
tablet for four users; nothing in it is specific to that hardware.

The end state:

- One SQLite database at `/srv/ddb/ddb.sqlite3`, read and written by
  every biologist under their own OS identity. WAL mode + a `ddb` group
  give concurrent GUI sessions safe coexistence.
- Every biologist has their own conda env and repo clone under their
  own `$HOME`, but points at the shared DB via `local_paths.json`.
- The Brother QL-820NWB label printer and the tablet's USB cameras
  work for every biologist without per-session reconfiguration.
- A system-level `systemd` timer snapshots the DB hourly, content-dedupes,
  and pushes changes to Google Drive via `rclone`. It runs regardless of
  who is (or isn't) logged in.

The guide is roughly in dependency order — steps 2–6 can be repeated
per new user; steps 1 and 7 are once per tablet.

!!! tip "Idempotent scripts"
    Every provisioning script in `scripts/` diffs against the current
    state and only makes changes when the target differs from the
    source. Safe to re-run after `git pull` or when adding another
    user.

---

## 1. Shared database at `/srv/ddb/`

Do this **once per tablet**, as an admin.

```bash
sudo bash scripts/setup_shared_sqlite.sh
```

What it does:

- Creates `/srv/ddb/` and `/srv/ddb/env-packs/` with mode `2775` and
  group `ddb` (the setgid bit means new files inherit the group
  automatically).
- Creates the `ddb` Unix group if it doesn't exist.
- Adds an empty `ddb.sqlite3` in that directory so a fresh install
  can `alembic upgrade head` against it.

Then add every biologist to the `ddb` group:

```bash
sudo usermod -aG ddb alice
sudo usermod -aG ddb bob
# ... they need to log out and back in for group membership to apply.
```

Point DDB at the shared paths — this is the committed template at the
repo root:

```bash
cp local_paths.template.json local_paths.json
$EDITOR local_paths.json
# → set "active_profile": "shared_tablet"
```

Migrate the schema against the shared file (once, from any admin
account that has the `ddb` env installed):

```bash
DDB_DATABASE_URL=sqlite:////srv/ddb/ddb.sqlite3 alembic upgrade head
```

Path resolution inside DDB is env var → active profile → in-repo
default, so `local_paths.json` is authoritative on the tablet.

!!! warning "Restoring an existing DB"
    If you're migrating from a pre-existing single-user DDB install,
    copy the old `.sqlite3` file into `/srv/ddb/ddb.sqlite3` **before**
    running `alembic upgrade head` — otherwise the migration will
    happen on an empty DB and you'll lose all vials and audit history.

---

## 2. Per-user install

Do this **once per biologist**, from their own account (no sudo).

```bash
curl -sSL \
  https://raw.githubusercontent.com/zerotonin/drosodb/main/scripts/install_user.sh \
  | bash
```

The one-liner:

1. Clones `drosodb` to `$HOME/PyProject/drosodb`.
2. Creates a `ddb` conda env in the user's own Miniforge (or falls
   back to `miniconda3` if that's what they have).
3. Installs the package editable with `pip install -e ".[gui,hardware,dev]"`.
4. Drops a launcher at `~/.local/bin/ddb-launcher` + a
   spec-compliant `~/Desktop/ddb.desktop` icon.

!!! tip "Speeding up user #2 and beyond"
    conda-forge downloads through the campus link collapse to
    ~100 KB/s when two conda processes run at the same time. Build
    an offline env-pack once — the README's *Offline env pack*
    section covers the one-time snapshot workflow — and later
    biologists' installs finish in seconds instead of tens of
    minutes.

---

## 3. Bluetooth printer access

The QL-820NWB pairs once at the OS level, then DDB opens an RFCOMM
channel to it from Python. Both halves need per-user setup: OS-level
group membership, and an app-level `.env` telling DDB the MAC address.

### 3a. Bluetooth group membership (admin, once per user)

```bash
sudo usermod -aG bluetooth alice
sudo usermod -aG bluetooth bob
# → they need to log out and log back in for it to take effect
```

BlueZ refuses D-Bus + RFCOMM socket access to users outside the
`bluetooth` group, so this is a hard prerequisite. Without it, DDB's
print calls fail with permission errors even if the `.env` is
correctly set up.

### 3b. Pair the printer (once per tablet)

The printer only needs to be paired to the tablet once, using any GUI
Bluetooth manager or `bluetoothctl`. Because the bond is stored in
`/var/lib/bluetooth/` (system-wide), all users see the paired printer
after step 3a — they don't each need to re-pair.

If pairing has drifted or the printer refuses to reconnect, the GUI's
Settings → Printer reconnect dialog has a guided flow (reset bond,
restart BT service, re-pair) that works from any biologist's account
provided they're in the `bluetooth` group.

### 3c. Printer `.env` per user

DDB's per-user config lives in `.env` inside each biologist's repo
clone. Two paths are equivalent — pick whichever fits.

**Self-service** — each biologist runs this from their own account,
no sudo needed:

```bash
bash ~/PyProject/drosodb/scripts/setup_my_printer.sh
```

The script:

- Finds the biologist's repo clone (default `$HOME/PyProject/drosodb`,
  falls back to a search).
- Writes `.env` with the tablet's printer settings.
- Runs a fresh `python -c "from ddb.config import settings; ..."` so
  the biologist can see what pydantic actually loaded.
- Checks `bluetooth` group membership and prints the admin fix if
  missing.

**Admin batch** — for provisioning several users in one root shell:

```bash
sudo bash scripts/write_user_printer_env.sh alice bob
# → with no args, defaults to the current tablet's user list
```

Both are idempotent. When something still doesn't work:

```bash
sudo bash scripts/diagnose_user_printer.sh
# → per user: clone location, .env content, cold-Python view,
#   bluetooth-group status, whether a stale GUI is holding old settings
```

!!! note "Restart the GUI after `.env` changes"
    `pydantic-settings` reads `.env` once at process start. If the
    biologist has DDB already open when the `.env` is written, they
    must fully close and reopen the GUI — closing the tab isn't
    enough.

---

## 4. Cameras

DDB uses two USB webcams (front + back) via V4L2 for QR scanning.
Every user needs two things: OS-level access to `/dev/video*`, and
DDB's per-user role → USB-bus-path mapping in
`~/.config/ddb/cameras.json`.

### 4a. OS-level camera access

On modern systemd/logind Ubuntu, `/dev/video*` is granted to the
**currently active** login session via a `uaccess` ACL. On a
single-user session this "just works", but shared-tablet setups have
two failure modes:

- **Fast user switching** can leave the just-switched-to session
  marked as `online` rather than `active`, so no ACL is installed.
- **Multiple concurrent sessions** (e.g. SSH plus a graphical login)
  only ACL the console one.

The bulletproof fix is a static `video` group membership — check
whether it's needed first:

```bash
# Is the biologist's session properly active?
loginctl user-status <username> | grep -E 'State|Active'
# → "State: active" means uaccess should be working already
```

If sessions are consistently `active`, no group change is required.
If any test call fails with `Permission denied` on `/dev/video*`:

```bash
sudo usermod -aG video alice
sudo usermod -aG video bob
# → log out + back in
```

### 4b. Camera role assignments per user

Which physical camera is the "front" and which the "back" is stored
in `~/.config/ddb/cameras.json` as a `role → USB bus_path` map:

```json
{"front": "1-6.1", "back": "1-6.2"}
```

USB bus paths are tied to the physical USB port on the tablet — they
survive reboots and camera swaps, and they're **identical for every
user on the same tablet**. So the admin's `cameras.json` can be
copied verbatim into every biologist's home.

```bash
# The admin (or any user whose own cameras.json is correct) runs:
sudo bash scripts/write_user_camera_config.sh
# → source: the invoking sudo user's ~/.config/ddb/cameras.json
# → targets: current tablet's user list
```

Alternate flow — a biologist runs `ddb camera assign` interactively
in their own terminal to pick which physical device plays each role.
That's the fallback if there's no admin `cameras.json` to copy from.

!!! note "No GUI restart needed"
    The Scan tab re-reads `cameras.json` every time the user clicks
    Start. After the config is written the biologist just clicks
    Start; they don't need to close and reopen the GUI.

---

## 5. Where DDB actually reads and writes

A reference table so you know which file each biologist owns and
which is shared. All paths that look project-relative resolve
against the repo clone in each user's home.

| Path | Owner | Purpose |
|---|---|---|
| `/srv/ddb/ddb.sqlite3` | shared (root:ddb 664) | The one and only DB. Every biologist writes here. |
| `/srv/ddb/backups/*` | shared (root:ddb 2775) | Hourly snapshot ladder — see §6. |
| `/srv/ddb/env-packs/*.tar.gz` | shared (root:ddb 2775) | Offline conda env packs for fast onboarding. |
| `$REPO/local_paths.json` | admin (committed template, gitignored real) | Points DDB at the shared paths on this tablet. |
| `$HOME/PyProject/drosodb/` | user | The biologist's git clone + editable install. |
| `$HOME/PyProject/drosodb/.env` | user | Per-user config — printer MAC, camera defaults, etc. |
| `$HOME/.config/ddb/cameras.json` | user | Per-user camera role → bus_path map. |
| `$HOME/miniforge3/envs/ddb/` | user | The biologist's conda env. |
| `/etc/ddb/backup.env` | admin | Backup timer's config — retention, rclone remote. |
| `/etc/ddb/rclone.conf` | admin (root 0644) | rclone remote credentials for offsite push. |
| `/usr/local/sbin/ddb-backup` | admin (root 0755) | The backup script itself, installed from `scripts/`. |
| `/etc/systemd/system/ddb-backup.{timer,service}` | admin | Systemd units triggering the backup. |

---

## 6. Off-device backup

The full walkthrough is in [operations.md](operations.md#offline-backups).
Recap so this guide stands on its own:

### 6a. Install the system timer

```bash
sudo bash scripts/setup_system_backup.sh
```

Installs `/usr/local/sbin/ddb-backup`, drops the systemd units, and
enables `ddb-backup.timer`. From now on the backup fires every hour
at `:00`, independent of any user session.

### 6b. Enable offsite push to Google Drive

Local snapshots survive DB corruption but not a stolen tablet. To
also push to Google Drive:

```bash
sudo apt install rclone
sudo rclone --config /etc/ddb/rclone.conf config
# → wizard: n (new) → name "gdrive" → drive → blank client_id/secret
# → scope 1 (full) → auto config → open browser, approve
```

Then wire the remote into the timer:

```bash
sudo $EDITOR /etc/ddb/backup.env
# → set:
#     DDB_BACKUP_RCLONE_CONFIG=/etc/ddb/rclone.conf
#     DDB_BACKUP_RCLONE_REMOTE=gdrive:drosodb-backup

sudo rclone --config /etc/ddb/rclone.conf mkdir gdrive:drosodb-backup

# First fire — pushes the full history (~34 MB) as the seed
sudo systemctl start ddb-backup.service
journalctl -u ddb-backup.service -n 30 --no-pager
```

### 6c. Content-addressed dedupe

The backup script SHA-256s each new snapshot and compares against
`.last-hash` (the digest of the last shipped one). If the content
matches, the local candidate is discarded, the rclone push is skipped,
and only `ddb.latest.sqlite3`'s mtime is touched to prove "we
checked, still current".

Practical consequence: **on hours when the DB didn't change, zero
bytes hit Google Drive**. Weekend + overnight traffic is nil; a busy
day pushes a handful of ~200 KB deltas.

!!! tip "Retention counts unique states, not hours"
    `DDB_BACKUP_RETAIN_HOURLY` (default 168) counts change events,
    not clock hours. On a low-turnover DB, the 168-entry ladder can
    cover months rather than one week — always tracing real changes,
    never idle repeats.

---

## 7. Troubleshooting cheat sheet

### Print button greyed out

Run the diagnostic — it names the specific fix per row:

```bash
sudo bash scripts/diagnose_user_printer.sh
```

Most common causes, ranked:

1. GUI was open when `.env` was written — pydantic reads `.env` once
   at process start. Close and reopen the GUI.
2. Biologist not in `bluetooth` group — `sudo usermod -aG bluetooth
   <user>` and log out + back in.
3. `.env` in the wrong repo (biologist has multiple clones) — the
   discovery search picks the first `pyproject.toml` naming `ddb` it
   finds; check the diagnostic's "found repo" line.

### Scan tab shows no cameras / "No camera assigned to role 'back'"

`~/.config/ddb/cameras.json` is missing. Copy from the admin:

```bash
sudo bash scripts/write_user_camera_config.sh <user>
```

Then the biologist clicks Start in the Scan tab again — no GUI
restart needed.

### `unable to open database file` in the backup log

The `ddb-backup.service` unit needs `ReadWritePaths=/srv/ddb` (not
just the subdir), because WAL-mode SQLite touches `-wal`/`-shm`
sidecars next to the source DB even for a read-only snapshot. Fixed
in `0cfdf56` — if you see this on an older tablet, `git pull` and
re-run `sudo bash scripts/setup_system_backup.sh`.

### Backup timer looks like it's firing twice

Almost always the old per-user cron still lives alongside the new
system timer:

```bash
crontab -l | grep -i drosodb
```

Delete the line (`crontab -e`) and remove the old script. The
system timer is enough.

### Two biologists using the tablet at the same time

Supported — SQLite WAL mode lets both GUIs read and write
concurrently. If a write ever gets stuck ("database is locked"), the
`busy_timeout` (5s) retries automatically. Practical hitting-the-wall
limit is around 3-4 concurrent writers; the tablet form factor caps
this well below that.

---

## 8. Not yet supported

- **Shared-account mode with per-user passwords.** The current model
  assumes each biologist has their own OS account (no login prompt in
  DDB itself). The plan when a lab wants password-per-user is a
  Settings toggle that swaps the OS-user resolver for a startup login
  dialog — filed as a follow-up.
- **Automatic printer config propagation.** Every user still needs
  their own `.env` written. The natural fix is a shared
  `/etc/ddb/printer.env` read on top of the per-user `.env` — filed
  as a follow-up.
- **Camera hot-plug.** DDB reads bus paths at Scan-tab startup; if you
  unplug and replug a camera during a session, restart the GUI to
  pick up the new device.
