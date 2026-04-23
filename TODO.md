# DDB — deferred work

Items we've noticed but consciously parked. When you pick one up, move its
bullet into the commit/PR that fixes it and delete it from here.

## Cleanup / maintenance

- [ ] **`brother_ql.devicedependent` deprecation warning** on every CLI/GUI
  start (`ddb gui`, `ddb printer test`, ...). Upstream `brother_ql` is in
  long-term maintenance mode. Options: filter the warning in
  `ddb.printing.raster`, or switch to a maintained fork (`brother_ql_next`
  on PyPI) and re-run the hardware smoke test.

## Infrastructure

- [ ] **GitHub Actions CI failing** (flagged when we were focused on getting
  the printer + DB usable). Likely missing the new runtime deps
  (`brother_ql`, `zxing-cpp`, conda-forge `rclone`) or a test that needs
  the real `.env`. Next time CI gets attention: look at the latest red run
  and adjust `environment.yml` / `.github/workflows/*.yml`.

## GUI

- [ ] **Settings tab still missing camera-role editing.** The tab now has a
  debug toggle and donor/user/org-unit quick-entry masks. Still missing:
  - Camera role assignments (front / back) editable from the UI, backed by
    `ddb.camera.config` — so swapping which webcam is "back" doesn't
    require re-running `ddb camera assign` in a terminal.
  - Possibly: printer MAC + label size + auto-print, currently `.env`-only.

## Scanning

- [ ] **OCR fallback for the print code.** If Micro QR still doesn't decode
  reliably on the back webcam, run tesseract (or easyocr) on the printed
  5-char code — those glyphs are big (48 px bold DejaVu) and far more
  blur-tolerant than QR modules. Whitelist the Crockford Base32 alphabet
  (0–9, A–Z minus I/L/O/U) to pin accuracy near 100 %. Needs
  `tesseract-ocr` (apt) + `pytesseract` (pip).

## Hardware notes (keep handy when CI/optics come up)

- The Star Lite 5's **back camera (Sunplus 1bcf:284d "USB 2.0 Camera")** is
  very soft (whole-frame Laplacian variance ≈17, ~3× worse than the front
  camera). Its UVC descriptor advertises `Focus, Auto` + `Focus (Absolute)`
  + `Zoom` but a 0→240 manual focus sweep produces **no change** in image
  sharpness — the controls are cosmetic. Good scanning happens on the
  front camera (Realtek 0bda:5830, sharpness ≈44, supports 1600×1200).
- Practical upshot: either re-assign roles so scanning happens on the
  front camera, or swap to larger labels (e.g. DK-11208 38×90mm) where
  the ~13mm vs ~35mm QR difference is decisive.
