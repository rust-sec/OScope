# OScope — Project Documentation

*Subject: Windows & Linux Internals and Commands*
*Edition: Windows-only implementation, with Linux `/proc` covered for comparison.*

---

## 1. Introduction

OScope is a small desktop application that shows the health of a Windows computer on one screen and
helps the user find which processes and folders use the most resources. It is written in Python with a
Tkinter interface. It is deliberately read-only: it observes the operating system, analyses what it sees,
and explains it. It does not modify anything.

## 2. Problem Statement

Computers slow down for many reasons: a process using too much CPU, memory running out, or a nearly full
disk. The information needed to tell these apart is spread over several tools (Task Manager, `tasklist`,
Resource Monitor, File Explorer property pages, PowerShell). A user needs a single simple view that
answers three questions: *Is something wrong? Which process is involved? Where did my disk space go?*

## 3. Motivation

* A practical utility that solves a problem every computer user has.
* A concrete way to study operating-system concepts, since every number on screen comes from an OS interface.
* A project small enough to explain fully in a viva.

## 4. Objectives

1. Retrieve real CPU, memory, storage, uptime and process data from Windows.
2. Present it in a responsive, readable interface that never freezes.
3. Provide a rule-based explanation of what the numbers suggest, without claiming unproven causes.
4. Provide a read-only folder analyzer that finds large folders and files.
5. Handle errors (access denied, vanished processes, missing folders) gracefully.
6. Keep the code small enough for a student to understand and modify.

## 5. Proposed Solution

Four views, one refresh engine, and a question-first front door (see `README.md` for the full feature list).

* **Ask OScope**: the user picks a question (*Why is my PC slow? Why is my RAM full? Why is my laptop hot or loud? Where did my storage go? What changed recently? Show me everything.*). The answer has four layers: what is happening, what is contributing, what the user may not have noticed, what they could consider. Every statement says whether it was **measured**, **seen together** with something else, or **could not be verified**.
* **System Health**: metric cards, a status card, machine details, and the state of every evidence source with its reason.
* **Process Analyzer**: processes grouped by program (Chrome = one row), searchable, sortable, with a details panel and top-5 lists.
* **Storage Analyzer**: cancellable scan with an interactive treemap (area = size), a synced folder tree, search, a file-type filter, notes on what could not be seen (unreadable folders, links, cloud-only files) and comparison between scans.

A background thread samples the system every 2 seconds (configurable) into a short window, so "high" means *sustained*. Collectors read one kind of
evidence each and report an honest availability. Detectors and rules turn the evidence into findings. An optional local history (SQLite) answers
"What changed recently?". A report generator saves everything as text. The guiding principle is **Observe → Analyze → Explain**, with evidence before action.

## 6. System Architecture

```text
GUI layer (Tkinter)         main_window · ask_view · overview_view · processes_view · storage_view (tree_panel, treemap) · dialogs
        ▲  queue (thread-safe hand-off, polled every 100 ms)
Thread layer                sampler (periodic) · probe threads (typeperf, PowerShell) · scan / search / history reads (on demand) · history writer
        ▼
Analysis layer (pure)       questions · detectors · relationship rules · trends · workloads · grouping · explain (wording rules)
Evidence layer              collectors/* : each returns Readings with an Availability (Available / Unavailable / Permission restricted / Not exposed / Not supported)
Data layer                  system_info · resource_manager · process_manager · storage_manager · tree_scanner
History layer               SQLite: recorder → writer thread → queries; storage snapshots and compare
OS-specific layer           platform_ops · windows_backend · collectors/windows  →  Win32 · Registry · performance counters · WMI · tasklist (+ psutil)
Utilities                   constants (thresholds, colours) · formatting · background · logging
```

Design decisions:

| Decision | Reason |
|---|---|
| Windows-specific code only in `platform_ops`, `windows_backend` and `collectors/windows` (a test enforces it) | A small, reviewable surface; injectable shims make it testable on any OS; collectors are chosen in one place so another OS could be added |
| A reading's value exists only when it was measured (`Reading` refuses a value otherwise) | Never invent data; say why something is missing |
| Analysis is pure (snapshot + recent window + history in, result out) | Easy to test; no hidden measurement |
| One writer thread owns every SQLite write | The GUI never waits on disk; errors are contained |
| Thresholds only in `constants.py` | Change one number, behaviour changes everywhere |
| Worker threads post to a queue, GUI drains it | Tkinter is not thread-safe; this keeps the UI responsive |
| Any unavailable metric returns `None` → "Unavailable on this platform" | Never invent data |
| Local files only: `settings.json`, optional `history.db`, reports, a log (in `%LOCALAPPDATA%\OScope`) | Settings and history survive restarts; nothing is sent anywhere |

## 7. Windows Internals Used

| Concept | How OScope uses it |
|---|---|
| **Win32 API** | `GlobalMemoryStatusEx` (memory), `GetTickCount64` (uptime), called through `ctypes` |
| **Registry** | Windows version and edition (`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion`), CPU name (`HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0`) |
| **Process and thread APIs** | Enumerated through psutil, which wraps native calls such as `NtQuerySystemInformation` and `GetProcessMemoryInfo` |
| **Performance counters / CPU times** | CPU utilisation = (busy time delta) ÷ (elapsed time delta) between two readings |
| **`tasklist` command** | Session name, window title (`/V`) and services hosted in a process (`/SVC`) |
| **File-system APIs** | `os.scandir` → `FindFirstFileW`/`FindNextFileW`; `shutil.disk_usage` → `GetDiskFreeSpaceExW` |
| **Reparse points** | Junctions and symbolic links carry the flag `FILE_ATTRIBUTE_REPARSE_POINT (0x400)`; the scanner skips them to avoid loops and double counting |
| **Access control** | Windows refuses some reads (`PermissionError` / `AccessDenied`); OScope counts and reports them |
| **Special processes** | PID 0 System Idle Process (hidden, idle time is not work); PID 4 System; `svchost.exe` hosts many services |

## 8. Linux Internals Used (comparison; not part of this edition's code)

Linux exposes kernel state through the **`/proc` pseudo file system**. Files there are generated on demand
by the kernel when read; they occupy no disk space.

| Need | Linux source | Windows equivalent used by OScope |
|---|---|---|
| Memory | `/proc/meminfo` (`MemTotal`, `MemAvailable`) | `GlobalMemoryStatusEx` |
| CPU model | `/proc/cpuinfo` | Registry `ProcessorNameString` |
| Uptime | `/proc/uptime` | `GetTickCount64` |
| CPU times | `/proc/stat` | system CPU times via psutil |
| One process | `/proc/<PID>/status`, `/proc/<PID>/stat` | psutil / `tasklist` |
| Process list | numeric directories in `/proc` | `psutil.process_iter` |
| Disk usage | `statvfs` (`df`) | `GetDiskFreeSpaceExW` |

Because OS-specific code is isolated (`platform_ops`, `windows_backend`, `collectors/windows`) and collectors are selected in one function
(`collectors/registry.py`), a Linux set of collectors (`/proc/pressure`, `/sys/class/hwmon`, `/sys/class/power_supply`) could be added without
changing the analysis or the GUI. It has not been built.

## 9. Process Management

* A **process** is a program in execution: its own virtual address space, handles, and one or more threads.
* The OS identifies it by a **PID** and records its **parent PID**, forming a process tree.
* Windows creates processes with `CreateProcess`; the scheduler runs *threads* on CPU cores.
* OScope shows for each process: name, PID, CPU %, memory (working set), status (*Running* or *Suspended*) and parent PID.
* **CPU %** = change in the process's CPU time ÷ elapsed time, divided by the number of logical CPUs, so 100% means the whole machine.
* Processes can exit while OScope reads them, or refuse access. Both are handled: the row disappears, or memory shows "—" and the process is counted as *restricted*.
* OScope **does not terminate processes.** Ending the wrong process can lose data or destabilise Windows, and the project's purpose is analysis.

## 10. Memory Management Concepts

* **Physical memory (RAM)** is fast, volatile and limited. **Storage** (SSD/HDD) is slower, persistent and larger.
* Each process sees its own **virtual address space**; Windows maps it to RAM in pages and can move pages to the **page file** on disk (**paging**).
* **Working set**: the pages of a process currently in RAM. This is the "Memory" column.
* **Private bytes / private working set**: memory used only by that process (shown in details as *Private memory*).
* **Available memory** includes memory that can be reclaimed quickly (such as cached file data on the *standby list*), so "used = total − available" follows Task Manager's *In use* idea.
* When RAM runs low, Windows trims working sets and pages to disk. The system slows because disk is far slower than RAM. OScope warns at 85% (`MEMORY_HIGH_PERCENT`).

## 11. File System Concepts

* NTFS stores files in directories; each entry has size, timestamps and attributes.
* **Traversal**: `os.scandir` streams a directory's entries; OScope walks the tree iteratively with an explicit stack (no recursion limit).
* **Sizes** are logical file sizes. A directory's size is the sum of its files. Only the 200 largest files are kept in memory (a min-heap), so memory use stays small even for millions of files.
* **Reparse points** (junctions, symlinks) are not followed.
* **Access control lists** can deny reading; OScope shows *Access denied* and carries on.
* OScope only calls `scandir` and `stat`. It never writes, moves, renames, deletes or changes permissions.

## 12. OS Commands Used

| Command / interface | Where | Purpose |
|---|---|---|
| `tasklist /V /FO CSV /NH /FI "PID eq n"` | process details | session name, window title |
| `tasklist /SVC /FO CSV /NH /FI "PID eq n"` | process details | services hosted in the process |
| Registry read (`winreg`) | system info | version, edition, CPU name |
| Win32 calls (`ctypes`) | resource / system info | memory, uptime |

Manual equivalents the panel may ask about: `tasklist`, `wmic cpu get name`, `systeminfo`, `wmic logicaldisk get size,freespace,caption`, `dir /s`.
`tasklist` is called only once per selected process (never in a loop), so OScope does not spawn subprocesses every refresh.

## 13. Implementation

| Module | Responsibility |
|---|---|
| `main.py` | OS check → dependency check → start GUI; shows friendly messages, never a traceback |
| `core/platform.py` | Detects OS; `UNSUPPORTED_MESSAGE` |
| `core/platform_ops.py` | The one place for OS differences the rest of the app needs: link detection, hidden/system/cloud file flags, app-data folder, elevation, system drive |
| `core/windows_backend.py` | Direct Win32 / registry / `tasklist` calls; each returns `None` on failure |
| `collectors/*` | Evidence: `Reading` + `Availability`; Windows collectors for memory commitment, paging, GPU, thermal/fan, power, startup; disk activity (psutil) |
| `analysis/*` | Process grouping, trends, detectors, relationship rules, questions, workloads, wording rules, the orchestrator |
| `history/*` | SQLite schema and migrations, recorder (downsampling), writer thread, retention, storage snapshots and compare, settings |
| `core/sampler.py` | Thread: collects `Snapshot` (CPU, memory, storage, uptime, processes, groups, collector readings, findings) each interval, keeps a ring buffer of recent samples; minimum 1 s gap so CPU % is meaningful |
| `core/process_manager.py` | `list_processes()`, `get_details(pid)` |
| `core/storage_manager.py` | `get_drive_usage()`, `DirectoryScanner` (streaming, cancellable, permission-tolerant) |
| `core/tree_scanner.py` | Full-tree scan: children always sum to the folder's size (smallest files folded), denied/linked/cloud-only/hidden reporting |
| `core/diagnostics.py` | `evaluate(cpu, mem, storage)` → list of `{level, title, message}` (the quick "right now" status on the Overview) |
| `core/report.py` | Builds and saves the text report |
| `gui/*` | Views and widgets; only the main thread touches widgets |

Diagnostic rules (`app/utils/constants.py`):

| Rule | Level |
|---|---|
| CPU ≥ 80% / ≥ 95% | warning / critical |
| Memory ≥ 85% / ≥ 95% | warning / critical |
| Storage ≥ 80% | info ("getting full") |
| Storage ≥ 90% / ≥ 95% | warning / critical ("low storage") |
| none of the above | normal |

Messages use cautious wording ("possible", "consider reviewing"); OScope never names a specific cause. The question-based answers use further
thresholds and a stricter wording guard: see `docs/EVIDENCE_AND_HONESTY.md`.

## 14. Testing

See `TESTING.md`. Summary: **417 automated tests** (collectors, analysis, history, scanner, the whole GUI under a virtual display)
plus manual Windows checks (`docs/WINDOWS_VERIFICATION.md`). The suite uses injected shims and hand-written samples (typeperf CSV, PowerShell
output, registry values) and simulates access-denied, links and cloud placeholders, so those paths are tested on any machine.
**The current version was developed on Linux, where the Windows-only calls cannot execute; the verification checklist marks everything that must
still be confirmed on a real Windows machine.**

## 15. Results

* All 417 automated tests pass on the development machine (Linux, Python 3.12, Tk under Xvfb); the GUI was also checked from screenshots at 1280x800 and 1366x768.
* The application starts on the question screen, answers every question on synthetic and live data, groups processes, scans folders (tree, treemap, search, filter, compare), keeps history, and produces a report, without freezing the interface (a slowed scan test confirms the GUI loop keeps ticking).
* Windows-specific behaviour (performance counters, WMI, registry, power API, file attributes, `tasklist`) is covered by parsing and availability tests on hand-written samples; the live calls need confirmation on Windows (see `docs/WINDOWS_VERIFICATION.md`).

## 16. Limitations

* Not a replacement for Task Manager, Resource Monitor or professional monitors.
* No automatic optimisation and no guarantee of root-cause identification.
* Cannot read protected processes or files without permission.
* Depends on Windows-specific interfaces and psutil.
* CPU % is an interval average and can miss short spikes.
* Sizes are logical sizes; hard links are counted once per name; very long paths may be skipped.
* Cloud-only (OneDrive) files are reported separately rather than counted; the attribute handling still needs checking on a real PC.
* Temperature and fan speed are shown only if the firmware exposes them, which many PCs do not; OScope says so instead of guessing.
* Disk activity is throughput, not "how busy"; per-process GPU and the Photoshop scratch location are not available.
* Windows only in this edition.

## 17. Future Scope

Application footprint and residual-data detection after uninstall · cleanup as analysis only · elevated scans · per-process GPU ·
services and scheduled-task startup analysis · history graphs · CSV/PDF export · Linux collectors · macOS support.

## 18. Conclusion

OScope shows that a compact program can gather real information from operating-system interfaces, analyse it
with transparent rules, and explain it in plain language, while staying read-only and responsive. Isolating OS-specific
code, keeping thresholds in one file, and separating GUI from data collection make it easy to explain, test and extend.
