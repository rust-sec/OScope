# OScope — Testing

## How to run

```bat
python -m unittest discover -s tests -v
```

On Linux (development): `OSCOPE_DEV=1 xvfb-run -a python3 -m unittest discover -s tests` (the GUI tests need a display; without one they are skipped).

The suite has **417 automated tests**:

| File | Covers |
|---|---|
| `tests/test_core.py` | formatting, diagnostics rules, platform, system info, processes, folder scanners, file categories, Windows backend parsing, entry point, report |
| `tests/test_foundation.py` | `Reading` rules, platform facade, settings, logging, sampler wiring, OS-isolation guard |
| `tests/test_collectors.py` | typeperf / PowerShell / registry parsing, availability mapping for every collector, background probes, `--probe`, process grouping, labels |
| `tests/test_analysis.py` | trends, detectors, every relationship rule (positive and negative), questions, workloads, the causal-wording guard, report integration |
| `tests/test_history.py` | database, migrations (including v1 to v2 upgrade), corrupt/locked files, recorder, writer thread, retention, queries, service, change detection, storage snapshots and compare |
| `tests/test_storage.py` | scanner: roll-ups, folding, hidden, cloud-only, denied, links, cancel, progress; tree helpers |
| `tests/test_treemap_layout.py` | squarified treemap layout |
| `tests/test_gui.py`, `tests/test_storage_gui.py` | the real window under a virtual display: Ask OScope, Overview, Processes (grouped and flat), Settings, history, Storage (tree/treemap/details sync, search, filter, navigation, progress, notes, compare) |
| `tests/test_docs_and_safety.py` | read-only guarantee, no network code, the one PowerShell script only reads, documented test count, no stale claims |

## Status vocabulary (read this first)

The current version was **built and tested on Linux**, where Windows-only calls (registry, `GlobalMemoryStatusEx`, performance counters, WMI,
power API, `tasklist`) cannot run. Results are therefore recorded in two kinds:

| Status | Meaning |
|---|---|
| **Pass (automated, Linux dev run)** | The automated test passed on the development machine. The OS-independent logic, the parsers (against hand-written samples) and the availability rules are confirmed. |
| **Pending: run on Windows** | Needs a real Windows 10/11 machine. The *Actual Result* cell is left for you to fill in. |

Run the automated suite on Windows first: it should report `OK` (417 tests; a few Windows-only tests are skipped elsewhere and run there).
Then walk through the manual cases below and `docs/WINDOWS_VERIFICATION.md` (one line per reading, with the command to capture real output: `python main.py --probe`).

The older cases below (T01 to T20) were recorded for the first version on macOS (2026-09-29) and are kept as the manual script; the automated tests behind them
have since been extended, not removed.

---

## Test cases

| Test ID | Input | Expected Result | Actual Result | Status |
|---|---|---|---|---|
| **T01 Windows startup** | `python main.py` on Windows 10/11 | Window opens on **System Health** within ~1 s; header shows "Windows" and "Last updated"; cards fill with real values; no console errors | GUI start verified on the dev machine in dev mode (window built, first snapshot received, ran 6 s with empty stderr; `test_01`). Live Windows run: *(fill in)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T02 Non-Windows startup** (replaces "Linux startup": this edition is Windows-only) | `python main.py` on macOS/Linux without `OSCOPE_DEV` | Message "Unsupported operating system. OScope currently supports Windows only."; exit code 1; no traceback | `EntryPointTests.test_unsupported_os_shows_message_and_exits_cleanly` passed | Pass (automated, macOS dev run 2026-09-29) |
| **T03 Process retrieval** | Open **Processes** | Table lists running processes with Process, PID, CPU, Memory, Status, Parent PID; PID 0 hidden; this app's own PID present | `ProcessTests.test_list_contains_this_process`, `GuiTests.test_03` passed (459 processes listed on the dev machine). Windows: *(fill in; compare a few rows with Task Manager)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T04 CPU retrieval** | Open **System Health** while running a busy program | CPU % in 0–100, rises under load; shows logical and physical core count | `SystemInfoTests.test_memory_and_cpu` passed. Windows: *(fill in; compare with Task Manager, expect ±10 points)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T05 Memory retrieval** | Open **System Health** | Memory card shows `used / total GB`, percent and available; used + available = total | `test_memory_and_cpu` passed (used + available = total). Windows uses `GlobalMemoryStatusEx`: *(fill in; total should equal installed RAM minus reserved)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T06 Storage retrieval** | Open **System Health** | Storage card shows `C:\`, used / total, percent, free | `SystemInfoTests.test_drive_usage`, `test_bad_drive_returns_none`, `WindowsBackendTests.test_system_drive_uses_environment_on_windows` passed. Windows: *(fill in; compare with File Explorer → This PC)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T07 Process search** | Type `chrome` (or any running name) in Search; then type `zzzz` | Only matching rows remain (name or PID match, not case-sensitive); no match shows "No matching processes found." | `GuiTests.test_03_process_table_search_and_sort` passed | Pass (automated, dev) · **Pending: run on Windows** |
| **T08 Process sorting** | Click the Process, PID, CPU, Memory headers; click a header twice | Rows reorder by that column; second click reverses; arrow shows direction; numeric columns sort numerically | `GuiTests.test_03` passed (PID ascending/descending, memory descending default) | Pass (automated, dev) · **Pending: run on Windows** |
| **T09 Directory scanning** | Storage → Select Folder → a folder with sub-folders and files | Total size, file count and directory count correct; largest directories listed by size; progress shown while scanning | `ScannerTests.test_counts_and_sizes` (exact counts and bytes), `GuiTests.test_05` passed. Windows: *(fill in; compare total with folder Properties)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T10 Permission denied** | Scan a folder containing an item Windows will not let you read (for example `C:\Windows\System32\config`, or a folder whose permissions deny your user) | Banner "Access denied: Some files or directories could not be analyzed because the operating system denied access."; the scan still finishes; no crash | `ScannerTests.test_permission_denied_is_counted_and_scan_continues` (simulated `PermissionError`) passed. Real Windows ACL denial: *(fill in)* | Pass (simulated) · **Pending: run on Windows** |
| **T11 Nonexistent directory** | Scan a path that does not exist / is a file | Message "The selected folder does not exist or is not a directory."; no traceback | `ScannerTests.test_nonexistent_directory`, `test_file_instead_of_directory`, `GuiTests.test_06` passed | Pass (automated, macOS dev run 2026-09-29) |
| **T12 Large-file detection** | Scan a folder with a file over the threshold; change threshold in the box next to the table or in Settings | Files ≥ threshold listed with File, Path, Size, biggest first; raising the threshold above every file shows "No files above the selected size threshold were found." | `ScannerTests.test_large_file_detection_sorted`, `test_only_n_largest_files_kept`, `GuiTests.test_05` passed | Pass (automated, dev) · **Pending: run on Windows** (try a real >500 MB file) |
| **T13 Report generation** | Click **Generate Report** | File `reports\oscope_report_YYYY-MM-DD_HHMMSS.txt` created; contains OS, health, memory, top processes, storage, diagnostic summary | `ReportTests.test_report_text_and_file`, `GuiTests.test_08` passed (name pattern and sections checked). Windows: *(fill in)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T14 GUI responsiveness** | Start a scan of a large folder (for example `C:\Users`) and click between views while it runs | Window keeps responding; progress counter updates; Stop button works | `GuiTests.test_07_gui_stays_responsive_during_scan`: a deliberately slowed scan ran while the Tk loop kept ticking, longest stall under 0.5 s. Manual on Windows: *(fill in)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T15 Diagnostic thresholds** | CPU ≥ 80, memory ≥ 85, storage ≥ 80 / 90 / 95 | Correct level and title; below threshold = normal; unavailable metric skipped | `DiagnosticsTests` (6 tests) passed | Pass (automated, macOS dev run 2026-09-29) |
| **T16 Read-only guarantee** | Scan a folder, compare it before/after | Names, sizes and modified times unchanged | `ScannerTests.test_scan_does_not_modify_anything` passed | Pass (automated, macOS dev run 2026-09-29) |
| **T17 `tasklist` parsing** | Select a process on Windows | Details show Session, Window title (if any) and, for `svchost.exe`, Hosted services | `WindowsBackendTests` (positional CSV parsing with fixtures, N/A handling, no-match) passed. Live `tasklist`: *(fill in)* | Pass (fixtures) · **Pending: run on Windows** |
| **T18 Process disappears** | Select a process, then close that program | Details panel says "This process is no longer running."; no error | `ProcessTests.test_details_for_missing_process` passed. Live: *(fill in)* | Pass (automated, dev) · **Pending: run on Windows** |
| **T19 Settings** | Settings → interval 5 s → Save; large-file threshold 100 | Refresh cadence changes; threshold box updates; invalid threshold (e.g. `abc`) is rejected with a message | Settings applied programmatically (`_apply_settings`) in a smoke run; dialog itself not automated: *(fill in)* | **Pending: run on Windows** |
| **T20 Windows-only calls are safe elsewhere** | Call registry / Win32 / tasklist helpers on non-Windows | All return `None` / empty, no exception | `WindowsBackendTests.test_windows_only_calls_are_safe_elsewhere` passed | Pass (automated, macOS dev run 2026-09-29) |

## Bugs found and fixed while testing

| Found by | Problem | Fix |
|---|---|---|
| `test_only_n_largest_files_kept` | Used `heapq.heappushreplace`, which does not exist, so scanning crashed once more than 200 files had been seen | Use `heapq.heapreplace` |
| `test_memorystatusex_matches_win32_size` | `c_ulong` is 4 bytes on Windows but 8 on 64-bit Unix, so the `MEMORYSTATUSEX` structure size differed | Use fixed-size `c_uint32` for the two DWORD fields |
| Layout measurement | Card sub-text and process-details panel requested more space than available at default window size | Wrapped sub-text, compact one-line detail rows, shrinkable table columns, window clamped to screen size |


## Added since the first version (see `docs/WINDOWS_VERIFICATION.md` for the Windows steps)

| Area | Automated here | Pending: run on Windows |
|---|---|---|
| Evidence readings (memory commitment, paging, GPU, thermal, fan, power, startup, elevation) | parsers and availability rules for every collector; "never a value unless measured" matrix | real outputs on a real PC; `--probe` standard vs administrator |
| Question-based answers | every question x every workload on synthetic machines; relationship rules positive and negative; causal-wording guard | read a few real answers for sense |
| Process grouping | grouping, restricted counts, aliases, GUI | Chrome/Edge on a real PC |
| History | schema, migrations, corrupt/locked files, writer thread, retention, queries | run for ~10 minutes, then *What changed recently?* |
| Storage | roll-ups, folding, denied/links/cloud/hidden (injected), tree/treemap sync, search, filter, compare | OneDrive placeholders, real ACL denials, junctions, High-DPI, `C:\` scan |

## Bugs found and fixed while building the current version

| Found by | Problem | Fix |
|---|---|---|
| New scanner test | The trailing partial batch of files in a folder was never trimmed, so a folder could keep more than its limit and fold the wrong files | Trim in `_finish_frame` |
| New scanner test | Symlinks were silently ignored (neither folder nor file) | Every symlink is counted and listed |
| New scanner test | Files inside a hidden folder were not counted as hidden | Hidden context is inherited |
| Reviewing `open_or_recreate` | Any `DatabaseError` (including "database is locked") would have moved a healthy history file aside as "corrupt" | Only real corruption recreates; locked/unopenable is re-raised; regression test |
| History tests | `context()` could read before the writer had opened the database | It waits for the writer to be ready first |
| Storage GUI test | Tk delivers selection events later, so a flag held during the call was already cleared and the echo could undo a quick double-click zoom | Swallow exactly the echoed selection |
| Screenshots | The new evidence card squeezed the status cards to nothing; the detail panel hid the *Show in File Explorer* button on 768-pixel screens | Compact card, minimum row height, compact detail panel |
| Storage GUI tests | A late background result could touch a destroyed widget; the progress bar kept animating; `BackgroundRunner.close()` left its timer pending | Guards, `destroy()` override, timer cancelled |
| Wording guard | A sentence used "because" while describing Windows caching | Reworded; the guard now covers every sentence of every answer |
