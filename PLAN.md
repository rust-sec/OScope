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
| Windows-only code | `core/windows_backend.py` | ALL Win32 / registry / `tasklist` calls, each guarded, each returns `None` if unavailable |
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
