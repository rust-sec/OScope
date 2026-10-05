# OScope — Project Summary

> **Status note (current version).** This summary describes the earlier read-only analyzer and its treemap. The product has since become a
> question-first, evidence-based tool: *Ask OScope*, per-program process grouping, honest availability for every reading, local history and
> "What changed recently?", and a much stronger Storage view (folder tree synced with the treemap, search, file-type filter, notes on what a scan
> could not see, and comparison between scans). **`README.md` is the current description**; `docs/EVIDENCE_AND_HONESTY.md` explains the rules the
> explanations follow and `docs/WINDOWS_VERIFICATION.md` lists what still needs a run on real Windows.


*A System Health and Resource Analyzer for Windows*

> Source material for the project slide deck. Each feature below is written as
> **What it is → Real-world problem it solves → How to use it**, so sections
> can be lifted almost directly onto individual slides. A suggested slide
> outline is at the very end.

---

## 1. The Problem

When a Windows PC feels slow, runs low on memory, or fills up its disk, the
information needed to diagnose *why* is scattered across half a dozen
different tools: Task Manager for processes, `tasklist`/PowerShell for
scriptable output, File Explorer's "Properties" for folder sizes, and a
separate third-party app (WinDirStat, WizTree) if you want to actually *see*
what's eating your storage. Most users don't know all these tools exist, and
switching between them to piece together one diagnosis is slow and
error-prone.

**OScope consolidates CPU, memory, process, and storage diagnostics into one
lightweight, read-only desktop application** — and, as of this build, adds an
interactive visual treemap so "what's using my disk space" is answered by
*looking at a picture* instead of reading a table.

**Design rule:** Observe → Analyze → Explain. OScope reads real operating-system
data, applies simple threshold rules, and explains the result in plain
language. It never kills a process, deletes a file, or changes a setting.

---

## 2. Who This Helps

- A student or professional whose laptop has slowed down and wants a fast,
  visual answer without learning Task Manager's advanced tabs.
- Anyone trying to free up disk space who doesn't want to install (and trust)
  a third-party disk-analysis tool.
- A systems/OS course project demonstrating real interaction with Windows
  internals (Win32 API, the registry, `tasklist`, the filesystem) rather than
  just wrapping a library call.

---

## 3. Feature-by-Feature Breakdown

### 3.1 System Health Dashboard
**What it is:** A single overview screen showing CPU %, memory used/total,
disk used/total, uptime, OS version, hostname, and a color-coded health
status, refreshed automatically every 2 seconds (configurable) with a manual
Refresh button.

**Problem it solves:** Today this information lives in three different
places (Task Manager's Performance tab, `systeminfo`, and Settings → System).
One glance here replaces all three.

**How to use it:** Open OScope — this is the screen it opens on. No setup
needed; numbers update live.

### 3.2 CPU Monitoring
**What it is:** Live CPU utilization percentage, logical and physical core
counts, and the CPU model name (read from the Windows registry), with a
color-coded bar and an automatic "High CPU Usage" warning above 80%.

**Problem it solves:** Users see "my PC is slow" but rarely check whether CPU
is actually the bottleneck versus memory or disk — this makes that
distinction immediate and visual.

**How to use it:** Visible on the Overview screen at all times; the bar turns
amber/red automatically when usage is high.

### 3.3 Memory (RAM) Monitoring
**What it is:** Total, used, and available RAM with a live percentage and
the same color-coded warning behavior as CPU (amber ≥ 85%, red ≥ 95%).

**Problem it solves:** Answers "do I need to close some programs?" without
opening Task Manager.

**How to use it:** Also on the Overview screen, updated on the same 2-second
cycle.

### 3.4 Interactive Storage Analyzer (Treemap) — the centerpiece feature
**What it is:** Select any folder or drive; OScope scans it in the
background and renders an **interactive treemap** — every file and folder
becomes a rectangle whose *area* is proportional to its size, and whose
*color* reflects its file type (Images, Videos, Audio, Documents, Archives,
Executables, Code, or Folder). This replaces "read a table of numbers" with
"look at a picture and instantly see the one huge orange block eating your
disk."

Built from scratch for this project:
- A **full-tree scanner** (`TreeScanner`) that walks the filesystem once and
  retains a full parent→child size tree (needed so every zoom level shows
  exact sizes), while staying safely bounded in memory on very large drives.
- A **squarified treemap layout algorithm** (the same family of algorithm
  used by WinDirStat/WizTree), implemented in pure Python with no extra
  dependency.
- **Click** a rectangle to select it and see its exact name, type, size (in
  bytes and human-readable form), and full path in a detail panel.
- **Double-click** a folder to zoom the treemap into it; a **breadcrumb bar**
  (`C:\ > Users > satya > Downloads`) lets you jump back out to any ancestor
  folder in one click.
- **Hover** any rectangle — even a tiny sliver too small to label — for a
  tooltip with its name, size, and path.
- **"Show in File Explorer"** opens a real Explorer window with the exact
  selected file or folder highlighted (read-only: it selects, it never runs
  or opens the file itself).
- A **Treemap / List toggle** keeps the original sortable "largest
  directories" bar list and "large files" table available as an alternative
  view.

**Problem it solves:** "What's taking up all my storage?" is normally
answered by manually right-clicking folders and checking Properties one at a
time, or by installing a separate third-party tool. This is now built in,
free, and read-only.

**How to use it:** Storage Analyzer → Select Folder (or the Downloads
shortcut) → watch the treemap fill in as the scan progresses → click to
inspect, double-click to drill into a folder, use the breadcrumb to back out,
hover for details on tiny files, and use "Show in File Explorer" to jump
straight to a file on disk.

### 3.5 Large File Finder
**What it is:** Within the treemap, large files are immediately visible as
big rectangles; in List mode, a dedicated table lists every file above a
configurable size threshold (default 500 MB) with its name, full path, and
exact size.

**Problem it solves:** Finds the "one 8 GB ISO you forgot about" without
manually browsing folders.

**How to use it:** Adjust the "Large file threshold (MB)" field in the
toolbar; the List view's table (and which files stand out largest in the
Treemap) updates instantly.

### 3.6 Largest Directory Analysis
**What it is:** In List mode, a bar-chart-style ranking of the largest
sub-folders under the scanned root. In Treemap mode, this is the same
information shown spatially — the biggest folder is simply the biggest
rectangle.

**Problem it solves:** Answers "which folder should I clean up first?" at a
glance.

**How to use it:** Automatically populated after any scan; toggle to List
mode for the ranked-list version.

### 3.7 Process Analyzer
**What it is:** A live table of every running process with PID, CPU %,
memory (working set), status, and parent PID.

**Problem it solves:** Replaces `tasklist` or Task Manager's Details tab for
users who want this without a command line.

**How to use it:** Processes screen — the table refreshes automatically.

### 3.8 Process Search
**What it is:** A search box that filters the process table live as you
type (e.g. typing "chrome" shows only Chrome processes).

**Problem it solves:** Finding one process among 200+ running ones by
scrolling is slow.

**How to use it:** Type into the Search field above the process table.

### 3.9 Process Sorting
**What it is:** Click any column header (Name, PID, CPU, Memory, Status) to
sort the process table by it, ascending or descending.

**Problem it solves:** "Which process is eating my RAM right now?" — sort by
Memory descending and the answer is the top row.

**How to use it:** Click a column header; click again to reverse the sort
order.

### 3.10 Process Details Panel
**What it is:** Selecting a process shows its executable path, owning user,
thread count, start time, parent process, and (for services like
`svchost.exe`) the specific Windows services it's hosting — read via
`tasklist /V` and `tasklist /SVC` on demand.

**Problem it solves:** Explains cryptic system processes ("what is this
`svchost.exe` actually doing?") in plain terms.

**How to use it:** Click any row in the process table.

### 3.11 Top CPU / Memory Processes
**What it is:** Quick top-5 rankings of the most CPU-hungry and
memory-hungry processes, surfaced both live in the app and in generated
reports.

**Problem it solves:** Directly answers "what is making my computer slow?"
without manual sorting.

**How to use it:** Visible automatically; also included in every generated
report (see 3.13).

### 3.12 System Diagnostics (Rule-Based Health Status)
**What it is:** A rule engine that compares live CPU/memory/storage
percentages against defined thresholds and produces plain-language findings
at four severity levels: Normal, Info, Warning, Critical (e.g. *"High memory
usage detected. Consider reviewing the processes using the most memory."*).

**Problem it solves:** Turns raw percentages into an actual verdict, so a
non-technical user doesn't have to know that "85% RAM" is worth worrying
about but "40% RAM" isn't.

**How to use it:** The status card on the Overview screen updates
automatically; the same findings appear in generated reports.

### 3.13 System Report Generation
**What it is:** A "Generate Report" button that writes a timestamped,
human-readable `.txt` report (e.g. `oscope_report_2026-09-30_074812.txt`)
containing OS info, CPU/RAM/disk usage, top processes, the last storage scan,
and the diagnostic summary.

**Problem it solves:** Gives something concrete to save, share, or attach to
a support ticket — a timestamped snapshot instead of a screenshot.

**How to use it:** Click "Generate Report" in the header at any time; OScope
offers to open the folder it was saved to.

### 3.14 Automatic & Manual Refresh
**What it is:** A background sampler thread updates CPU/memory/process data
every 2 seconds (configurable in Settings) without ever freezing the window;
F5 or the Refresh button forces an immediate update.

**Problem it solves:** Keeps the dashboard current without the user having
to do anything, while staying responsive even during a large folder scan.

**How to use it:** Happens automatically; press F5 or click Refresh for an
immediate update.

### 3.15 Error & Permission Handling
**What it is:** Every failure mode is handled gracefully: access-denied
files/folders are counted and skipped (never crash the scan), a process that
disappears mid-scan is simply excluded, an invalid folder shows a clear
message instead of a traceback, and an unsupported OS shows a one-line
explanation and exits cleanly.

**Problem it solves:** A tool that crashes on a permission error is worse
than useless for exactly the messy, real-world folders users actually want
to scan (e.g. `C:\Windows`, `C:\Users\<other user>`).

**How to use it:** No action needed — this shows up as OScope simply
"working" on folders that would make other tools error out.

### 3.16 Read-Only Operation (Safety by Design)
**What it is:** A hard project boundary: OScope never kills a process,
deletes or modifies a file, writes to the registry, or changes any system
setting. "Show in File Explorer" *selects* a file — it deliberately does not
offer an "Open File" action, since that would execute arbitrary programs.

**Problem it solves:** Removes an entire class of risk (accidental data loss,
accidentally killing a critical system process) that comes with most
"optimizer" tools, and makes the project far simpler to reason about and
demo safely.

**How to use it:** Nothing to configure — it's a guarantee, not a setting.

### 3.17 Windows Process Integration
**What it is:** Direct use of Windows-native interfaces: the Win32 API via
`ctypes` for memory/uptime, the registry for OS version and CPU model, and
`tasklist` for session/window-title/hosted-service information — all
isolated in a small layer (`platform_ops`, `windows_backend` and `collectors/windows`) so the rest of the app stays
OS-agnostic.

**Problem it solves:** Demonstrates real operating-system interaction rather
than relying entirely on a third-party library — directly relevant to an OS
internals course.

**How to use it:** Invisible to the end user; visible in the codebase as the
small layer where the Windows-specific calls live.

---

## 4. Known Limitations / Not Yet Implemented

Being transparent about scope matters for a project defense:

- **Cross-platform (Linux) support is not implemented.** This build is
  Windows-only by design; on any other OS it prints an "Unsupported
  operating system" message and exits cleanly rather than crashing. A Linux
  backend (reading `/proc/meminfo`, `/proc/cpuinfo`, `/proc/uptime`,
  `/proc/<PID>/status`) is architecturally straightforward to add later since
  all OS-specific code is already isolated in a small layer, but it has not
  been built.
- **No "Open File" action** in the treemap by design (see 3.16) — only
  "Show in File Explorer" (select, don't execute).
- The treemap shows one zoom level at a time (with breadcrumb navigation)
  rather than an infinitely nested view — a deliberate simplification that
  keeps the rendering fast and legible.

---

## 5. Engineering Quality

- **417 automated tests** across core logic (scanning, diagnostics, reports,
  Windows backend parsing), the treemap layout algorithm (area conservation,
  no overlaps, correct proportions), and full GUI interaction tests (search,
  sort, click-to-select, double-click-to-zoom, breadcrumb navigation) — all
  passing.
- **Found and fixed a real Windows quirk**: the "Show in File Explorer"
  button initially failed to select the correct file whenever its path
  contained a space (the vast majority of real Windows paths), because of
  how `explorer.exe`'s `/select,` switch interacts with command-line
  quoting. Fixed and covered by a regression test.
- Background scanning and live sampling run on separate threads and hand
  results back to the UI thread through a thread-safe queue, so the window
  never freezes — verified by a test that scans 250 files while asserting
  the UI loop never stalls more than 0.5 seconds.

---

## 6. Technology Stack

- **Python 3.11+** and **Tkinter** for the GUI (no external UI framework).
- **psutil** — the only third-party dependency — for process/CPU data.
- Python standard library: `ctypes` (Win32 API), `winreg`, `subprocess`
  (`tasklist`, Explorer), `os.scandir` (filesystem traversal), `threading`,
  `queue`, `heapq`, `unittest`.
- A hand-written **squarified treemap algorithm** (no charting/plotting
  library) — kept dependency-free to match the project's "stay small,
  reliable, easy to explain" philosophy.

---

## 7. Suggested Slide Outline

A straightforward mapping of the sections above onto a deck:

1. **Title slide** — OScope: A System Health and Resource Analyzer for Windows
2. **The Problem** (§1) — one slide, the scattered-tools pain point
3. **Design Philosophy** — Observe → Analyze → Explain, read-only by design (§1, §3.16)
4. **System Health Dashboard** (§3.1) — screenshot + bullet list
5. **CPU & Memory Monitoring** (§3.2–3.3) — one slide, two halves
6. **Storage Analyzer: Before** — the old "list of largest files" approach (one line, for contrast)
7. **Storage Analyzer: The Treemap** (§3.4) — the flagship slide; screenshot + feature bullets (click / double-click / breadcrumb / hover / Show in Explorer)
8. **How the Treemap Works** — one technical slide: full-tree scan → squarified layout → colored by file type
9. **Process Analyzer** (§3.7–3.11) — search, sort, details, top consumers
10. **System Diagnostics** (§3.12) — the four severity levels, example messages
11. **Reports & Refresh** (§3.13–3.14)
12. **Reliability & Safety** (§3.15–3.16) — error handling + read-only guarantee
13. **Under the Hood** (§3.17, §6) — Windows APIs used, tech stack
14. **Testing** (§5) — 417 tests, the real bugs found and fixed
15. **Limitations & Future Work** (§4) — Linux support, honesty about scope
16. **Demo / Closing slide**
