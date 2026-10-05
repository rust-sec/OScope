# OScope

**An explainable system-intelligence and storage-analysis tool for Windows**

*It uses operating-system information to help you understand your computer: what is happening, what the evidence is, and what you may have overlooked. It does not ask you to understand the operating system.*

> **Edition note:** this build is **Windows only**. On any other operating system OScope prints
> *"Unsupported operating system. OScope currently supports Windows only."* and exits cleanly.
> A developer switch (`OSCOPE_DEV=1`) lets the OS-independent logic run elsewhere; Windows-only readings then
> report *Not supported on this OS*. Linux `/proc` is explained in the documentation for comparison only.
>
> **Verification note:** this version was developed and tested on Linux. Everything that does not need Windows
> (parsing, availability rules, diagnostics, history, scanning, the whole GUI) is covered by automated tests.
> The Windows-specific reads (performance counters, WMI, registry, power API, file attributes) are tested against
> hand-written samples and still need a run on a real PC: see [`docs/WINDOWS_VERIFICATION.md`](docs/WINDOWS_VERIFICATION.md).

---

## What it is

Most tools show numbers: `RAM 91%`, `CPU 74%`. OScope's job is the step after that.

> *Memory use is high: 91% in use over the last 58 seconds. Chrome has 24 processes and Photoshop is running.
> Disk activity was high at the same time. The system drive has 7% free.*

Every answer has the same four layers:

1. **What is happening**: a plain-language observation.
2. **What is contributing**: the measurements behind it (which programs, how much).
3. **What you may not have noticed**: relationships between measurements that are easy to miss.
4. **What you could consider**: optional, secondary, and never an automatic action.

OScope is a **transparency** tool, not an optimizer. It is **read-only**: it never ends a process, deletes a file or
changes a setting.

### Honest by construction

* A number is shown only when it was really measured. If it cannot be (no sensor, no permission, not supported),
  OScope says which of those it is and why, instead of guessing.
* It separates **Measured**, **Seen together** and **Could not verify**. It may say two things were seen together; it
  never says one made the other happen. A test scans every sentence it can produce for causal wording.
* "Sustained" means most of the recent window, so one spike never raises a flag.

See [`docs/EVIDENCE_AND_HONESTY.md`](docs/EVIDENCE_AND_HONESTY.md).

## Features

| Area | What it does |
|---|---|
| **Ask OScope** (opens first) | Pick a question: *Why is my PC slow? · Why is my RAM full? · Why is my laptop hot or loud? · Where did my storage go? · What changed recently? · Show me everything.* Choose what you mostly do (general, gaming, video editing, Photoshop/design, music/audio, rendering/3D); that only changes which evidence comes first, never a threshold. *Copy as text*, *Ask again*. |
| **Overview** | CPU, memory, system drive, uptime, rule-based status, machine details, and **"What OScope can measure on this PC"**: every evidence source with its state, plus a *Details* window with the reason and source of each reading. Shows whether OScope runs as administrator. |
| **Processes** | Processes **grouped by program** (Chrome = one row with its processes underneath; toggle to the flat list), search, sort, top 5 CPU and memory, details panel. Group memory is labelled a *working-set total* because shared memory can be counted more than once. |
| **Storage** | Pick a folder (or Downloads, or the system drive). Interactive treemap where **area = size**, a synced **folder tree**, **search by name**, a **file-type legend that filters**, exact sizes, hidden items marked, **Show in File Explorer**, a **List** mode with sortable large files, Back / Esc / right-click navigation, a live progress bar showing the folder being read. |
| **What the scan could not see** | Unreadable folders are marked *size unknown, not zero* and listed by path; links that were not followed are counted; cloud-only OneDrive files are reported separately (they take no space on this disk). A *Details* window explains how sizes are measured. |
| **Compare scans** | Finished scans are remembered. Two scans of the same folder compare by file type and by folder, with a warning when they are not like-for-like (different amounts readable, one run as administrator). |
| **History** | Optional, local only: about one row of numbers every 30 seconds, for "What changed recently?". On/off and *Clear history* in Settings. |
| **Report** | `Generate Report` writes a text report with the evidence table and OScope's assessment. |
| **`--probe`** | `python main.py --probe` prints what every collector reports on this machine (for checking and for bug reports). |

Not included on purpose: process termination, file deletion or cleaning, "optimizers", registry cleaning, fan control,
networking, accounts, cloud, AI services.

## What OScope can and cannot measure

| Reading | How | Reality on a typical Windows PC |
|---|---|---|
| CPU, memory, system-drive space, uptime, processes | psutil, Win32 | Always available |
| Memory commitment (RAM + pagefile promised vs its limit) | `GetPerformanceInfo` | Available |
| Paging activity (Pages/sec) | `typeperf` (performance counters) | Available on English Windows; counter names are language-specific |
| Disk throughput (read / write per second) | psutil counters | Available; says *how much* data moves, not how busy the disk is or which program |
| GPU load | `GPU Engine` performance counters (the figure Task Manager uses: the busiest engine) | Needs a modern GPU driver; per-process GPU is not shown |
| Power source, battery, battery saver | `GetSystemPowerStatus` | Available (a desktop reports *no battery*) |
| Windows power mode | `PowerGetEffectiveOverlayScheme` | Named only for recognised modes, otherwise *unavailable* |
| Startup programs | Registry Run keys, Startup folders | Not a complete boot list (services and scheduled tasks are not included) |
| **Temperature** | WMI ACPI thermal zones | Often **not exposed** or **needs administrator rights**; a zone is not necessarily the CPU |
| **Fan speed** | WMI | **Not exposed**: Windows has no measured-speed property here, so OScope never shows an RPM |

## Technologies

* **Python 3.11+** and **Tkinter**; **psutil** (the only third-party package); SQLite from the standard library for history.
* Standard library: `ctypes`, `winreg`, `subprocess` (read-only commands only), `os.scandir`, `sqlite3`, `threading`, `queue`.
* Windows facilities: Win32 API, registry, performance counters (`typeperf`), WMI through PowerShell (`Get-CimInstance`), `tasklist`.

## Architecture

```text
┌─────────────────────────────── GUI (Tkinter, main thread) ───────────────────────────────┐
│ Ask OScope · Overview · Processes · Storage (tree + treemap + details) · Dialogs         │
└──────▲───────────────────────────▲──────────────────────────────▲───────────────────────┘
       │ questions                 │ results via a thread-safe queue│
┌──────┴────────────┐   ┌──────────┴─────────┐          ┌──────────┴─────────┐
│ analysis (pure)   │   │ Sampler thread     │          │ scan / search /    │
│ questions, rules, │◄──┤ snapshot every 2 s │          │ history reads      │
│ detectors, trends │   │ + ring buffer      │          │ (on demand)        │
└──────▲────────────┘   └──────────┬─────────┘          └──────────┬─────────┘
       │ facts                     ▼                               ▼
       │            collectors (one per kind of evidence) ·  tree_scanner · history
       │            each returns Readings with an honest Availability
       │                           │                       (history thread = the only SQLite writer)
       │                platform_ops · windows_backend  ← the only code that touches Windows directly
       └────────── Win32 · registry · performance counters · WMI · psutil
```

Layers, from the master design: UI → question → diagnostic orchestrator → evidence collectors → analysis → explanation → optional actions.
Worker threads never touch widgets: results go on a queue that the main thread drains every 100 ms
(`app/utils/background.py`). Slow Windows reads (PowerShell, `typeperf`) run on their own probe threads so sampling is never blocked.

## Windows implementation

| Information | Interface |
|---|---|
| Physical memory, uptime | `GlobalMemoryStatusEx`, `GetTickCount64` (kernel32, `ctypes`) |
| Commit charge and limit | `GetPerformanceInfo` (psapi) |
| Power source / battery | `GetSystemPowerStatus` (kernel32) |
| Power mode | `PowerGetEffectiveOverlayScheme` (powrprof) |
| Windows version, CPU model | Registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`, `...\CentralProcessor\0` |
| Pages/sec, GPU engines | `typeperf` with `\Memory\Pages/sec` and `\GPU Engine(*)\Utilization Percentage` |
| Temperature, fans | `Get-CimInstance` on `MSAcpi_ThermalZoneTemperature` and `Win32_Fan` (one PowerShell call, read-only) |
| Startup programs | Registry `Run` keys, `StartupApproved` state, Startup folders |
| Elevation | `IsUserAnAdmin` (shell32), display only |
| Hidden / system / cloud-only files | File attributes from `os.scandir` |
| Process list, CPU %, memory | `psutil` (wraps the Windows process APIs) |
| Process session, window title, hosted services | `tasklist /V`, `/SVC`, once, on demand |
| Folder scanning | `os.scandir` (`FindFirstFileW`); links are not followed |
| Dark title bar, sharp text | `DwmSetWindowAttribute`, `SetProcessDpiAwareness` (cosmetic) |

**"Memory" for a process** is its working set (`rss` in psutil), which can differ a little from Task Manager's default column.

## Linux `/proc` (comparison only, not used in this edition)

On Linux the kernel publishes live state as files: `/proc/meminfo`, `/proc/stat`, `/proc/<PID>/status`, and (where the kernel has it)
pressure-stall information in `/proc/pressure/{cpu,memory,io}`; temperatures and fans appear under `/sys/class/hwmon` when a driver exposes them.
OS-specific code is isolated in `platform_ops`, `windows_backend` and `app/collectors/windows/`, and collectors are chosen in one place
(`app/collectors/registry.py`), so a Linux set of collectors could be added without touching analysis or the GUI. Not built.

## Installation

Requires **Windows 10 or 11** and **Python 3.11+** (Tkinter is included with the python.org installer).

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running

```bat
python main.py            :: the application
python main.py --probe    :: print what every collector reports on this PC (no window)
```

Tests (**417 automated tests**; GUI tests need a display, on Linux use `xvfb-run`):

```bat
python -m unittest discover -s tests -v
```

On a non-Windows machine, for development: `OSCOPE_DEV=1 python main.py`.

## Where OScope keeps its data

Everything stays on this PC, in `%LOCALAPPDATA%\OScope\` (override with the `OSCOPE_DATA_DIR` environment variable):

| File | What |
|---|---|
| `settings.json` | refresh interval, large-file threshold, workload, history on/off |
| `history.db` | optional history (SQLite): numbers, program names, folders you scanned; no command lines, window titles or file contents |
| `reports\` | generated text reports |
| `oscope.log` | OScope's own diagnostic log |

Nothing is sent anywhere. OScope contains no network code.

## Project structure

```text
oscope/
├── main.py                     entry point (also --probe)
├── app/
│   ├── collectors/             evidence: Availability/Reading types, registry of collectors, probe, labels
│   │   └── windows/            memory, GPU, thermal/fan, power, startup, typeperf + PowerShell helpers
│   ├── analysis/               pure explanation logic: facts, trends, detectors, rules, questions, workloads, orchestrator, grouping, wording
│   ├── history/                SQLite: schema + migrations, recorder, writer thread, queries, storage snapshots and compare, settings
│   ├── core/                   platform, platform_ops, windows_backend, system_info, process_manager, tree_scanner, tree_utils, sampler, report, diagnostics
│   ├── gui/                    ask_view, overview, processes, storage (tree_panel, treemap, widgets), dialogs, components
│   └── utils/                  constants, formatting, background runner, logging, file categories
├── tests/                      unittest suite (see TESTING.md)
├── docs/                       EVIDENCE_AND_HONESTY.md, WINDOWS_VERIFICATION.md
└── PLAN.md  PROJECT_DOCUMENTATION.md  PROJECT_SUMMARY.md  PANEL_QA.md  TESTING.md
```

## Limitations

OScope:

* is **not** a replacement for Task Manager, Resource Monitor or professional hardware monitors;
* does **not** optimise, clean or fix anything, and does not claim to find root causes: it shows evidence and relationships;
* cannot read what Windows protects from the current user. It reports what it could not read and continues; it never bypasses Windows security;
* shows temperature and fan speed **only if the PC's firmware exposes them** (often it does not);
* measures disk *throughput*, not how busy the disk is, and cannot tell which program the traffic belongs to;
* sizes are logical file sizes; disk usage can differ (cluster rounding, compression); hard links are counted once per name;
* the treemap keeps the largest files of a very large folder and groups the rest as "(N smaller files)"; folder sizes are always exact;
* cloud-only files are reported separately and are not counted as space on this disk (the attribute handling needs checking on a real OneDrive PC);
* very long paths (over 260 characters) may be skipped unless Windows long-path support is enabled;
* `typeperf` counter names are language-specific, so paging and GPU readings are *unavailable* on non-English Windows.

## Postponed (decided, not forgotten)

Application footprint and residual-data detection after uninstall · cleanup (analysis only, never silent deletion) ·
elevated scans · per-process GPU · detecting a Photoshop scratch drive · audio-latency diagnosis · services and scheduled-task startup analysis ·
Linux collectors.

## Academic relevance

Built for *Windows & Linux Internals and Commands*. It demonstrates: process identification (PID/PPID) and grouping; CPU utilisation as a rate;
physical memory vs commit charge vs paging; working set vs private bytes; the registry; Win32 through `ctypes`; performance counters and WMI;
file-system traversal, attributes, reparse points and access control; sampling, trends and why a single instant misleads; and multi-threading with a safe
hand-off to a GUI. See `PROJECT_DOCUMENTATION.md` and `PANEL_QA.md`.
