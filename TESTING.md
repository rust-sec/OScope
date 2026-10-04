# OScope — Testing

## How to run

```bat
python -m unittest discover -s tests -v
```

* `tests/test_core.py`: 36 tests, no window needed.
* `tests/test_gui.py`: 8 tests that open the real window briefly (skipped automatically if no display).

## Status vocabulary (read this first)

The project was **built and tested on macOS**, where Windows-only calls (registry, `GlobalMemoryStatusEx`,
`tasklist`) cannot run. So results are recorded honestly in two kinds:

| Status | Meaning |
|---|---|
| **Pass (automated, macOS dev run 2026-09-29)** | The automated test passed on the development machine. The OS-independent logic is confirmed. |
| **Pending: run on Windows** | Needs a real Windows 10/11 machine. The *Actual Result* cell is left for you to fill in when you run it. |

Some cases have both: the logic passed in the automated run, and the live Windows behaviour is pending.
Run the automated suite on Windows first: it should report `OK` (44 tests). Then walk through the manual cases.

Automated run recorded: **44 tests, 0 failures, macOS 27, Python 3.12, Tk 9.0, 2026-09-29.**

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
