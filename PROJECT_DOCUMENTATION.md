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

Three views, one refresh engine.

* **System Health**: metric cards, a status card, and machine details.
* **Process Analyzer**: searchable, sortable process table with a details panel and top-5 lists.
* **Storage Analyzer**: recursive, cancellable folder scan that reports sizes, largest directories and large files.

A background thread samples the system every 2 seconds (configurable). A rule engine converts the numbers
into findings. A report generator saves everything as text. The guiding principle is **Observe → Analyze → Explain**.

## 6. System Architecture

```text
GUI layer (Tkinter)         main_window · overview_view · processes_view · storage_view · dialogs · components
        ▲  queue (thread-safe hand-off, polled every 100 ms)
Thread layer                sampler (periodic)  ·  scan thread (on demand)  ·  detail lookup (on demand)
        ▼
Data layer                  system_info · resource_manager · process_manager · storage_manager
Analysis layer              diagnostics (rules) · report (text output)
OS-specific layer           windows_backend  →  Win32 API · Registry · tasklist   (+ psutil)
Utilities                   constants (thresholds, colours) · formatting · background
```

Design decisions:

| Decision | Reason |
|---|---|
| All Windows-specific calls in `windows_backend.py` | One place to review, mock in tests, and replace with a Linux backend later |
| Thresholds only in `constants.py` | Change one number, behaviour changes everywhere |
| Worker threads post to a queue, GUI drains it | Tkinter is not thread-safe; this keeps the UI responsive |
| Any unavailable metric returns `None` → "Unavailable on this platform" | Never invent data |
| No database, no config file | Nothing needs to persist; settings live in memory |

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

Because OS-specific code is isolated, a Linux backend would replace `windows_backend.py` and leave the GUI unchanged.

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
| `core/windows_backend.py` | Every Win32 / registry / `tasklist` call; each returns `None` on failure |
| `core/sampler.py` | Thread: collects `Snapshot` (CPU, memory, storage, uptime, processes, findings) each interval; minimum 1 s gap so CPU % is meaningful |
| `core/process_manager.py` | `list_processes()`, `get_details(pid)` |
| `core/storage_manager.py` | `get_drive_usage()`, `DirectoryScanner` (streaming, cancellable, permission-tolerant) |
| `core/diagnostics.py` | `evaluate(cpu, mem, storage)` → list of `{level, title, message}` |
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

Messages use cautious wording ("possible", "consider reviewing"); OScope never names a specific cause.

## 14. Testing

See `TESTING.md`. Summary: 44 automated tests (36 without GUI, 8 driving the real window) plus manual
Windows test cases. The automated suite mocks `tasklist` output and simulates access-denied so those paths
are tested on any machine. **The project was developed on macOS, where the Windows-only calls cannot execute;
TESTING.md marks every result that still has to be confirmed on a real Windows machine.**

## 15. Results

* All 44 automated tests pass on the development machine (macOS, Python 3.12, Tk 9.0), 2026-09-29.
* On that machine the application starts, refreshes, searches, sorts, scans folders, handles a missing folder and produces a report, all without freezing the interface (a slowed scan test confirms the GUI loop keeps ticking, longest gap under 0.5 s).
* Windows-specific behaviour (registry reads, `GlobalMemoryStatusEx`, `tasklist`) is covered by parsing tests and a structure-size test, but the live calls need confirmation on Windows (see TESTING.md, "Pending").

## 16. Limitations

* Not a replacement for Task Manager, Resource Monitor or professional monitors.
* No automatic optimisation and no guarantee of root-cause identification.
* Cannot read protected processes or files without permission.
* Depends on Windows-specific interfaces and psutil.
* CPU % is an interval average and can miss short spikes.
* Sizes are logical sizes; cloud placeholder files can inflate totals; very long paths may be skipped.
* Windows only in this edition.

## 17. Future Scope

Historical resource graphs · process tree · network monitoring · startup-application analysis ·
configurable alerts · graphical disk map · CSV/PDF export · Linux backend using `/proc` · macOS support.

## 18. Conclusion

OScope shows that a compact program can gather real information from operating-system interfaces, analyse it
with transparent rules, and explain it in plain language, while staying read-only and responsive. Isolating OS-specific
code, keeping thresholds in one file, and separating GUI from data collection make it easy to explain, test and extend.
