# OScope — Build Plan (Windows-only edition)

**Scope decision:** the original brief was cross-platform (Windows + Linux). This build targets
**Windows only**. There is no Linux `/proc` layer. On any other OS the app prints/shows
"Unsupported operating system" and exits cleanly. (A developer-only switch, `OSCOPE_DEV=1`,
lets the logic run on a non-Windows machine for testing; Windows-only metrics then show
"Unavailable on this platform".)

Principle: **Observe -> Analyze -> Explain.** Read-only. No kill / delete / optimise.

## Architecture

| Layer | Files | Job |
|---|---|---|
| Entry | `main.py` | OS check, dependency check, start GUI, never show a traceback |
| Platform | `core/platform.py` | Detect OS, supported/unsupported message |
| Windows-only code | `core/platform_ops.py`, `core/windows_backend.py`, `collectors/windows/*` | Direct Win32 / registry / `tasklist` / counter / WMI access, each guarded, each returns an honest "unavailable" (see the 2026 evolution below) |
| Data | `system_info.py`, `resource_manager.py`, `process_manager.py`, `storage_manager.py` | Static info, CPU/RAM sampling, process list + details, drive usage + folder scanner |
| Analysis | `diagnostics.py`, `report.py` | Rule engine (thresholds from `constants.py`), text report |
| Concurrency | `core/sampler.py`, `utils/background.py` | Background sampling thread; thread-safe hand-off to the Tk main thread |
| GUI | `gui/*` | Dark theme, header, sidebar, 3 views, settings + about dialogs |

## Key design choices

1. **`psutil`** for CPU %, per-process data (one dependency, allowed by the brief). Underlying
   Windows APIs are documented, and **direct Win32 calls via `ctypes`** are used for memory
   (`GlobalMemoryStatusEx`) and uptime (`GetTickCount64`) so the OS concepts stay visible.
2. **`tasklist`** is used on demand (one call when a process is selected) for session name,
   window title and hosted services (`/SVC`) — parsed by column position, never by header text.
3. **GUI never blocks:** one sampler thread every 2 s (default) -> queue -> Tk `after()` poll.
   Storage scan runs in its own thread with progress posts and a cancel flag.
4. **Storage scan** = iterative `os.scandir` (no recursion limit), does not follow symlinks or
   junctions, counts permission errors and keeps going, keeps only the 200 largest files in a heap.
5. **Honest data:** anything unobtainable shows "Unavailable on this platform".
6. **Diagnostics** never claim a cause — "possible", "consider reviewing".

## Build order

1. utils (constants, formatting, background runner)
2. core (platform, windows_backend, system_info, resource_manager, process_manager,
   storage_manager, diagnostics, sampler, report)
3. unit tests for everything that has no GUI
4. GUI (components -> views -> main window -> dialogs)
5. main.py
6. Docs: README, PROJECT_DOCUMENTATION, TESTING, PANEL_QA, LICENSE
7. Self-review against the 15-point checklist; fix; report what could NOT be verified here

## 2026 evolution (question-first, evidence-based)

The original build was a read-only system monitor with a storage treemap. The current direction is an *explainable system-intelligence tool*:
**What is happening? What is contributing? What might you not have noticed? What could you consider?** Decisions taken with the project owner:

* Windows only this semester; Tkinter stays; evidence before action; no deletion code (cleanup, if ever added, is analysis only).
* Phases delivered: (1) foundation: collectors base, platform facade, settings, logging; (2) evidence collectors and per-program process grouping;
  (3) question-based diagnostics (detectors, relationship rules, workloads, wording guard); (4) local history in SQLite and "What changed recently?";
  (5) storage upgrades: tree + treemap sync, search, filter, honest scan notes, snapshot compare; (8-lite) docs and guard tests.
* Postponed: application footprint / residual detection, cleanup analysis, elevated scans, per-process GPU, Photoshop scratch detection, audio-latency diagnosis, Linux collectors.
* What is verified where: see `README.md` ("Verification note") and `docs/WINDOWS_VERIFICATION.md`.
