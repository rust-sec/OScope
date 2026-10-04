# OScope

**A System Health and Resource Analyzer for Windows**

*A lightweight utility for process, CPU, memory, and storage analysis using Windows operating-system interfaces.*

> **Edition note:** the original brief described a Windows + Linux tool. This build is **Windows only**.
> On any other operating system OScope prints *"Unsupported operating system. OScope currently supports Windows only."*
> and exits without crashing. Linux `/proc` is explained in the documentation for comparison, but is not used by the code.

---

## Overview

OScope puts the most useful "why is my PC slow?" information on one screen: CPU load, memory, disk space,
uptime, the processes using the most resources, and which folders and files eat your storage.

Its design rule is **Observe → Analyze → Explain**. It reads system information, applies simple rules,
and explains the result in plain language. It is **read-only**: it never ends a process, deletes a file,
or changes a setting.

## Problem Statement

Users often experience slow computers, high CPU or RAM usage, and low disk space, and cannot tell what is
responsible. The information needed to diagnose this is spread across Task Manager, `tasklist`, `wmic`/PowerShell,
File Explorer, and similar tools. OScope consolidates the essentials in one simple interface.

## Objectives

1. Retrieve real operating-system information (processes, CPU, memory, storage) without inventing data.
2. Present it in a clear, responsive desktop interface.
3. Explain what the numbers mean, without claiming causes it cannot prove.
4. Demonstrate how an application talks to OS interfaces.
5. Stay small, reliable and easy to explain.

## Features

| Area | What it does |
|---|---|
| **System Health** | CPU %, memory (used / total / available), system-drive usage, uptime, OS, hostname, CPU model and core count, auto-refresh every 2 s, manual Refresh |
| **System Status** | Rule-based findings (normal / info / warning / critical) with cautious wording |
| **Process Analyzer** | Table of Process, PID, CPU, Memory, Status, Parent PID; search; sort by any column; top 5 CPU and top 5 memory; details panel (executable, user, threads, start time, session, window title, hosted services) |
| **Storage Analyzer** | Pick any folder; total size, file and directory counts; largest sub-directories as bars; large-file finder (default 500 MB, adjustable); live progress; Stop button; access-denied handling |
| **Report** | `Generate Report` writes a timestamped text report into `reports/` |
| **Settings / About** | Refresh interval, large-file threshold, theme info; About dialog |

Not included on purpose: process termination, file deletion, "optimizers", networking, database, cloud, AI features.

## Technologies Used

* **Python 3.11+** and **Tkinter** (GUI)
* **psutil** (the only third-party package) for CPU %, process list and per-process data. See *Windows Implementation* for why, and for the Windows APIs it wraps.
* Python standard library: `ctypes`, `winreg`, `subprocess`, `os.scandir`, `shutil`, `threading`, `queue`, `heapq`, `unittest`
* Windows facilities: Win32 API, registry, `tasklist`

## System Architecture

```text
┌──────────────────────────── GUI (Tkinter, main thread) ─────────────────────────────┐
│  Header · Sidebar · Overview view · Processes view · Storage view · Dialogs         │
└───────────────▲──────────────────────────────────────────────▲──────────────────────┘
                │ results are posted through a thread-safe queue│
        ┌───────┴──────────┐                            ┌───────┴─────────────┐
        │ Sampler thread   │                            │ Scan / detail thread │
        │ every N seconds  │                            │ (on demand)          │
        └───────┬──────────┘                            └───────┬─────────────┘
                ▼                                               ▼
   resource_manager · process_manager · storage_manager · system_info · diagnostics
                                     │
                           windows_backend.py   ← the ONLY file with Windows-specific calls
                                     │
                 Win32 API · Registry · tasklist · psutil (wraps Win32)
```

Key point: worker threads never touch widgets. They put results on a queue; the main thread drains the
queue every 100 ms (`app/utils/background.py`). This is what keeps the window responsive.

## Windows Implementation

| Information | Interface used |
|---|---|
| Physical memory total / available | `GlobalMemoryStatusEx` (kernel32) called directly through `ctypes` |
| Uptime | `GetTickCount64` (kernel32) through `ctypes` |
| Windows version / edition | Registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion` plus `sys.getwindowsversion()` (build ≥ 22000 = Windows 11) |
| CPU model | Registry `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString` |
| CPU utilisation | `psutil.cpu_percent`, which reads system CPU times from Windows (busy time ÷ elapsed time between two readings) |
| Process list, CPU %, memory | `psutil.process_iter`, which uses Windows process APIs (for example `NtQuerySystemInformation`, `GetProcessMemoryInfo`) |
| Process session, window title, hosted services | `tasklist /V` and `tasklist /SVC`, run **once, on demand**, when you select a process; parsed by column position because header text is translated on non-English Windows |
| System-drive usage | `shutil.disk_usage` → `GetDiskFreeSpaceExW` |
| Folder scanning | `os.scandir` → `FindFirstFileW` / `FindNextFileW`; reparse points (junctions/symlinks) are skipped |
| Dark title bar, sharp text | `DwmSetWindowAttribute`, `SetProcessDpiAwareness` (cosmetic) |

**Why psutil?** Reading per-process CPU % correctly needs the *change* in each process's CPU time between two
readings and careful handling of processes that appear, vanish or deny access. psutil does this reliably.
OScope still calls the Win32 API directly for memory and uptime, and uses the registry and `tasklist`
directly, so the OS concepts stay visible in the code.

**"Memory" for a process** is its *working set*: the physical RAM currently assigned to it (`rss` in psutil). Task Manager's default
column shows a slightly different measure (private working set), so numbers can differ a little.

## Linux `/proc` (comparison only, not used in this edition)

On Linux the kernel publishes live system state as files in `/proc`. A Linux edition of OScope would read:
`/proc/meminfo` (memory), `/proc/cpuinfo` (CPU model), `/proc/uptime`, `/proc/stat` (CPU times), and
`/proc/<PID>/status` and `/proc/<PID>/stat` (one process each). Every process has its own directory named
by its PID. Nothing is stored; the kernel generates the text when you read it. The architecture already
isolates OS-specific code in one module, so a Linux backend would sit next to `windows_backend.py`.

## How Processes Are Identified

A **process** is a running program with its own virtual address space and at least one thread.
The OS gives each one a unique number, the **PID**, and records its **parent PID** (the process that
started it). OScope lists every process it may see, identified by `(PID, name)`, and shows the parent PID so you
can see who started what. PID 0 (System Idle Process) is hidden because its "CPU" figure is *idle* time, not work.
Some system processes refuse to reveal their memory to a normal user; they are still listed and counted as "restricted".

## Installation

Requires **Windows 10 or 11** and **Python 3.11+** (Tkinter is included with the python.org installer).

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Running the Application

```bat
python main.py
```

Run the automated tests:

```bat
python -m unittest discover -s tests -v
```

Developer switch: on a non-Windows machine, `OSCOPE_DEV=1 python main.py` starts the GUI for development.
Windows-only fields (CPU model, Windows edition, `tasklist` extras) then show *Unavailable on this platform*.

## Project Structure

```text
oscope/
├── main.py                    entry point: OS check, dependency check, start GUI
├── requirements.txt           psutil
├── README.md  LICENSE
├── PLAN.md                    build plan
├── PROJECT_DOCUMENTATION.md   academic write-up
├── TESTING.md                 test cases and results
├── PANEL_QA.md                answers to likely panel questions
├── app/
│   ├── core/
│   │   ├── platform.py          detects the OS, unsupported message
│   │   ├── windows_backend.py   ALL Windows-specific calls (Win32, registry, tasklist)
│   │   ├── system_info.py       static facts: OS, hostname, CPU model, uptime
│   │   ├── resource_manager.py  live CPU % and memory
│   │   ├── process_manager.py   process list and per-process details
│   │   ├── storage_manager.py   drive usage and read-only folder scanner
│   │   ├── diagnostics.py       rule engine (thresholds come from constants.py)
│   │   ├── sampler.py           background thread that collects snapshots
│   │   └── report.py            text report builder and saver
│   ├── gui/
│   │   ├── main_window.py  overview_view.py  processes_view.py  storage_view.py
│   │   ├── dialogs.py           Settings and About
│   │   └── components.py        cards, progress bar, buttons, table helper, theme
│   └── utils/
│       ├── constants.py         colours, thresholds, defaults, messages
│       ├── formatting.py        bytes, percent, duration, timestamps
│       └── background.py        thread-safe hand-off to the GUI thread
├── tests/                     test_core.py (no GUI) and test_gui.py
└── reports/                   generated reports
```

## Demo (3–5 minutes)

1. **Launch** `python main.py`. Point out CPU, Memory, Storage, Uptime and the status card.
2. **Processes**: click the *Memory* header to sort. Explain PID, Process, CPU, Memory, Parent PID. Type `chrome` in Search.
3. **Select a process**: show details. Select a `svchost.exe` to show *Hosted services* (from `tasklist /SVC`).
4. **Storage**: click *Downloads* (or *Select Folder*). Show largest directories and large files; change the threshold to 100 MB.
5. **Generate Report**: open the file in `reports/`.
6. **Explain**: the OS-specific code is only in `windows_backend.py`; the GUI never blocks because scanning and sampling run on threads.

## Screenshots

*Add screenshots to `docs/screenshots/` after running on Windows*: System Health, Process Analyzer with details open, Storage Analyzer with results, a generated report.

## Limitations

OScope:

* is **not** a replacement for Task Manager, Resource Monitor or professional monitoring tools;
* does **not** optimise, clean, or fix anything automatically;
* does **not** guarantee it finds the root cause of a performance problem. A high reading says *that* a resource is busy, not *why*;
* cannot read protected processes or files without the right permissions (it reports "Access denied" and continues);
* depends on Windows-specific interfaces and on `psutil`;
* CPU % is an average over the last refresh interval, so short spikes can be missed;
* file sizes are logical sizes (what Explorer shows as "Size"), not disk allocation; cloud placeholder files (e.g. OneDrive) may count their full size;
* the large-file list keeps the 200 biggest files found, and junctions/symlinks are not followed;
* very long paths (over 260 characters) may be skipped unless Windows long-path support is enabled.

## Future Improvements

Historical resource graphs · process-tree view · network monitoring · startup-application analysis ·
configurable alerts · graphical disk map · export to CSV/PDF · a Linux backend · macOS support.

## Academic Relevance

Built for *Windows & Linux Internals and Commands*. It demonstrates: process identification (PID / PPID);
CPU utilisation as a rate; physical memory vs storage; working set vs private bytes; the Windows registry;
the Win32 API through `ctypes`; command-line tools (`tasklist`) as an OS interface; file-system traversal,
reparse points and access control (permission denied); and multi-threading with a safe hand-off to a GUI.
See `PROJECT_DOCUMENTATION.md` and `PANEL_QA.md`.
