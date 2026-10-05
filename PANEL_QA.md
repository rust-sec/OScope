# OScope — Panel Q&A

Short, accurate answers. Where a question touches Linux, the answer explains the Linux mechanism and then how
this Windows-only build does the equivalent.

---

### Q1. What is a process?
A **process** is a program that is running. When you double-click an application, Windows creates a process:
it gets its own private **virtual address space**, a table of open handles (files, devices), a security identity,
and one or more **threads**, which are the units the scheduler actually runs on a CPU core.
A *program* is a file on disk; a *process* is that program in execution.

### Q2. What is a PID?
The **Process ID**: a unique number the OS gives each running process, so it can be referred to unambiguously
(names are not unique; many `chrome.exe` processes can exist). Each process also records its **parent PID**, the
process that created it. PIDs can be reused after a process exits. In OScope, PID and Parent PID are two table columns.
On Windows, PID 0 is the *System Idle Process* (a bookkeeping entry for idle CPU time) and PID 4 is the *System* process.

### Q3. How does the OS manage processes?
* **Creation and identity**: the OS creates the process (Windows: `CreateProcess`), assigns a PID, and records the parent.
* **Scheduling**: the scheduler shares CPU cores between *threads* in small time slices, by priority.
* **Memory**: each process has virtual memory; the memory manager maps pages to RAM and pages some out to disk when RAM is short.
* **Protection**: one process cannot read another's memory; access is checked against security descriptors (why some queries return *Access denied*).
* **Termination**: when a process exits, the OS reclaims its memory and handles.
OScope only *observes* this; it never changes it.

### Q4. How does your application retrieve process information?
Through `psutil.process_iter()`, which calls the Windows process APIs (for example `NtQuerySystemInformation` and
`GetProcessMemoryInfo`) and returns name, PID, parent PID, status, working-set memory and CPU time. CPU % is the
**change in a process's CPU time between two readings divided by the elapsed time**, divided by the number of logical CPUs.
When you select a process, OScope also runs `tasklist /V` and `tasklist /SVC` for that one PID to get the session, window
title and hosted services, on a background thread. I chose psutil because it handles processes that vanish or refuse access;
the direct Windows calls (registry, `GlobalMemoryStatusEx`, `tasklist`, performance counters, WMI) live in `windows_backend.py` and
`collectors/windows/`, so they are visible and not hidden behind the library.

### Q5. What is `/proc`?
On Linux, `/proc` is a **pseudo file system**: it looks like a directory of files, but it is not on disk. The kernel
generates the content when you read it. It exposes live system state: `/proc/meminfo` (memory), `/proc/cpuinfo`, `/proc/uptime`,
`/proc/stat` (CPU times), and one numbered directory per running process. Reading `cat /proc/meminfo` is asking the kernel a question.
This Windows-only build does not use `/proc`; the documentation gives the mapping (`PROJECT_DOCUMENTATION.md`, section 8).

### Q6. Why does Linux have `/proc/<PID>`?
It follows the Unix idea that "everything is a file", so ordinary tools (`cat`, `ls`, `grep`) can inspect the system without special APIs.
Each directory `/proc/<PID>/` describes one process: `status` (name, state, memory, parent PID), `stat` (raw counters such as CPU
ticks), `cmdline`, `fd/` (open files), `maps` (memory map). Tools like `ps` and `top` are essentially programs that read these files.
The Windows counterpart is the set of process APIs and the performance counters, which are reached through function calls rather than files.

### Q7. What is the difference between RAM usage and storage usage?
| | RAM (memory) | Storage (disk) |
|---|---|---|
| Speed | very fast | much slower |
| Persistence | lost at power-off | kept |
| Size | GBs | hundreds of GBs to TBs |
| What uses it | running programs and cached data | files, installed software |
| Full means | system slows and may page to disk | cannot save or install; updates fail |

OScope reports them separately: the Memory card comes from `GlobalMemoryStatusEx`; the Storage card is the system drive's used space.

### Q8. What happens when RAM usage becomes very high?
Windows first reclaims memory that is easy to give up (cached data, unused pages, *working-set trimming*). Then it moves less-used
pages to the **page file** on disk (**paging**). Disk is orders of magnitude slower than RAM, so programs become sluggish; heavy
paging is called **thrashing**. In extreme cases Windows shows a "low memory" warning and applications can fail to allocate memory.
OScope raises a *warning* at 85% and *critical* at 95% (`constants.py`) and points you at the processes using the most memory.

### Q9. How does your application work differently on Windows and Linux?
**This build targets Windows only**; on another OS it shows *"Unsupported operating system."* The design, however, isolates OS-specific
code in `platform_ops`, `windows_backend` and `collectors/windows` (a test enforces this), and the GUI and analysis logic never touch OS interfaces directly.
* **Windows (implemented):** Win32 API through `ctypes` (memory, uptime), registry (version, CPU name), `tasklist`, and psutil, which uses Windows process APIs.
* **Linux (design only):** `/proc/meminfo`, `/proc/cpuinfo`, `/proc/uptime`, `/proc/<PID>/stat|status`, `statvfs`.
Adding Linux would mean writing a second set of collectors (for example `/proc/pressure/*`, `/sys/class/hwmon`) chosen in `collectors/registry.py`; the analysis and the GUI would not change.

### Q10. Why did you choose Python?
Fast to build and easy for a student to read; Tkinter ships with Python, so the GUI needs no extra install; the standard library covers
threading, file traversal, the registry (`winreg`) and Win32 calls (`ctypes`); and one extra package (psutil) covers process data.
The trade-off is that Python is not the lightest option; OScope is a diagnostic tool, not a real-time system, so that cost is acceptable.

### Q11. Why SQLite, and why only now?
The first version had nothing to remember, so it used no database. "What changed recently?" and "Compare scans" need history: a
measurement about every 30 seconds, the biggest programs, and summaries of folder scans. That is a good fit for SQLite: it is in Python's standard
library (no new dependency), it is one local file, it supports the before/after queries directly, and retention is a simple `DELETE`.
It stays on the user's PC (`%LOCALAPPDATA%\OScope\history.db`), stores only numbers, program names and folders the user chose to scan, can be turned off or cleared
in Settings, and is written by a single thread so the window never waits on disk.

### Q12. Why didn't you implement process termination?
It is outside the project's purpose and it is risky. Ending the wrong process can lose unsaved work or crash Windows, and a tool that
can damage the system needs confirmations, protected-process lists, elevation handling and undo, which is a much larger and riskier project.
OScope follows **Observe → Analyze → Explain**: it tells you *which* process to look at and leaves the decision to the user and to Task Manager.
Keeping it read-only also makes the tool safe to demonstrate on any machine.

### Q13. What happens if permission is denied?
The OS refuses the read and Python raises `PermissionError` (or psutil raises `AccessDenied`). OScope catches it and **continues**:
* **Storage scan**: the item is counted, skipped, and at the end a banner reads *"Access denied. Some files or directories could not be analyzed because the operating system denied access."* Totals cover what could be read.
* **Processes**: a protected process still appears in the table; its memory shows "—" and the header counts it as restricted. In the details panel the field says *Access denied*.
No raw traceback is ever shown to the user.

### Q14. How do you prevent the GUI from freezing?
The Tkinter main thread only draws and handles clicks. Slow work runs on **background threads**: a *sampler* thread collects system data
every 2 seconds, a *scan* thread walks folders, and a short thread fetches process details. Widgets are **not** thread-safe, so the threads
never touch them: they put results on a `queue.Queue`, and the main thread empties that queue every 100 ms with `root.after`
(`app/utils/background.py`). The scanner also reports progress periodically and checks a cancel flag, so the **Stop** button works.
An automated test slows a scan on purpose and checks that the GUI loop never stalls for more than half a second.

### Q15. What are the limitations of the application?
* Not a replacement for Task Manager or professional monitors; it gives lightweight diagnostic information.
* It does not optimise or fix anything automatically, and it cannot guarantee it finds the root cause: a high reading says *that* a resource is busy, not *why*.
* It cannot read protected processes or files without permission.
* It depends on Windows-specific interfaces and on psutil.
* CPU % is an average over the refresh interval, so brief spikes can be missed; a process's "Memory" is its working set, which differs slightly from Task Manager's default column.
* File sizes are logical sizes; cloud placeholder files may inflate totals; long paths may be skipped.
* This edition supports Windows only.
Stating these openly is deliberate: they define what the tool can honestly claim.

---

## Extra questions a panel may ask

**What is CPU utilisation, exactly?** The fraction of time the CPU was busy over an interval. OScope takes two readings of system CPU time and computes `busy delta ÷ total delta`. That is why the first reading needs a short warm-up.

**What is a thread?** The unit of execution the scheduler runs. A process contains one or more threads that share its memory.

**What is the working set?** The pages of a process that are currently in physical RAM. Private bytes are the part not shared with other processes.

**What is `svchost.exe`?** A generic host process in which many Windows services run. OScope's *Hosted services* line (from `tasklist /SVC`) shows which ones a given `svchost.exe` contains.

**Why hide PID 0?** It is the System Idle Process; its "CPU" value is the *idle* time, so showing it at the top of a CPU list would be misleading.

**Why not follow junctions or symlinks?** They can point back up the tree (infinite loops) or to another drive (double counting). Windows marks them with the reparse-point attribute, which the scanner checks.

**How do you know your numbers are right?** CPU, memory, and storage values can be compared side by side with Task Manager and File Explorer; automated tests check the arithmetic (used + available = total, exact folder sizes), and the layer that could not run on the development machine is listed in `TESTING.md` as *Pending: run on Windows*.
