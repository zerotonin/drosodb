# DDB — notes for coding assistants

## Keep the user manual in step with the GUI

`docs/manual/index.md` is the user manual. The GUI's Help button and F1
show it in-app, and mkdocs publishes it. Biologists follow it step by
step, so a stale manual is a bug.

Any change that affects what a user sees or does must update the manual
**in the same commit**. That covers new or renamed buttons, tabs,
dialogs, settings, confirmations, workflows, keyboard shortcuts, and
changed behaviour of an existing control. Concretely:

1. Edit the relevant section of `docs/manual/index.md`, written for a
   lab biologist, not a developer.
2. If the change is visible on a tab or in a pictured dialog, re-render
   the screenshots: `python scripts/make_manual_screenshots.py`. The
   script uses a throwaway demo database and never touches real data.
3. A new tab needs an entry in `TAB_SECTIONS` (`src/ddb/manual.py`) so
   F1 lands on its section.
4. Rebuild the PDF copy: `python scripts/build_manual_pdf.py` writes
   `docs/manual/ddb_manual.pdf`. The PDF is not committed.

`tests/test_manual.py` fails when a button, checkbox or group box exists
that the manual never names, when a link or image is broken, or when
mkdocs-only syntax appears. Qt's Markdown renderer prints `!!! tip`
literally, so write tips as `> **Tip:** …` blockquotes.
