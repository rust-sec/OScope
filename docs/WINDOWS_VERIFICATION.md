# Windows verification checklist

OScope was developed and tested on Linux. The OS-independent logic, the parsers, the availability rules and the
whole GUI are covered by automated tests, but **what a real Windows PC returns has not been observed yet.**
The Windows-specific code is therefore tested against sample outputs written by hand from the documented formats,
and the items marked **WIN-VERIFY** below must be confirmed on a real machine.

## Step 1. Capture what this PC reports

```bat
python main.py --probe > probe_standard.txt
:: then again from an Administrator prompt:
python main.py --probe > probe_admin.txt
```

Paste the two files back (or compare them with the table). Each line is `reading  status  value` followed by the
source and the reason for any gap. Real samples let the hand-written test fixtures be replaced with real captures.

## Step 2. Compare each reading

| ID | Reading | How to check | Expected | Actual | OK? |
|---|---|---|---|---|---|
| W01 | `disk.read_bps`, `disk.write_bps` | Task Manager → Performance → Disk; copy a large file while running `--probe` | Rates rise during the copy; same order of magnitude as Task Manager | | |
| W02 | `mem.commit_percent` | Task Manager → Performance → Memory → *Committed* | `used/limit` matches the *Committed x / y GB* line | | |
| W03 | `mem.pages_per_sec` | `typeperf "\Memory\Pages/sec" -sc 1` in a prompt | A non-negative number, same order as typeperf. On non-English Windows expect **Unavailable** with a "counter names are language-specific" reason | | |
| W04 | `gpu.utilization`, `gpu.busiest_engine` | Run a game/video; Task Manager → Performance → GPU | Close to the busiest engine Task Manager shows (e.g. 3D, Video Encode). No GPU or old driver → **Unavailable** | | |
| W05 | `temp.acpi_max` | Standard user, then Administrator. Compare with BIOS/HWiNFO if available | Standard user: probably **Permission restricted**. Administrator: a plausible °C, or **Not exposed** on many consumer PCs. The value is an ACPI zone, not necessarily the CPU | | |
| W06 | `fan.rpm` | n/a | **Not exposed by hardware**, always (Windows has no measured-RPM property here). A number here would be a bug | | |
| W07 | `power.on_battery`, `power.battery_percent`, `power.charging`, `power.battery_saver` | Unplug and plug a laptop; turn Battery saver on | Values follow within a few seconds. Desktop: **Not exposed** with "no battery" | | |
| W08 | `power.mode` | Settings → System → Power → Power mode: cycle the choices | Names *Best power efficiency / Balanced / Better performance / Best performance*. **WIN-VERIFY:** the overlay GUIDs in `app/collectors/windows/power.py` come from memory; an unrecognised one must show **Unavailable (unrecognised power mode)**, never a guess | | |
| W09 | `startup.entries`, `startup.enabled_count` | Task Manager → Startup apps | Registry and Startup-folder entries appear; entries disabled in Task Manager show as disabled; Task Manager may list more (services and scheduled tasks are not included, by design) | | |
| W10 | Elevation | Run normally and as Administrator | Overview shows "Standard user…" / "Running as administrator" | | |

## Step 3. The application

| ID | Check | Expected | Actual | OK? |
|---|---|---|---|---|
| A01 | Start `python main.py` | Opens on **Ask OScope**; no console errors; header says Windows | | |
| A02 | Ask *Show me everything* | Sections and findings appear; every reading shows a state; nothing is a number that Task Manager contradicts | | |
| A03 | Processes → *Group by program* | Chrome/Edge are one row with their processes underneath; group memory is the sum; flat view still works | | |
| A04 | Storage → scan your user folder **including OneDrive** | **WIN-VERIFY:** cloud-only (online-only) files are counted in the banner as "cloud-only files … not counted", not in the sizes; locally available files are counted. Compare with OneDrive's "Always keep on this device" | | |
| A05 | Storage → scan a folder with items you cannot open (e.g. another user's folder) | They are listed under *Details…* by path, marked *size unknown, not zero*; the scan finishes | | |
| A06 | Storage → a folder with a junction/symlink | Counted under "links … were not followed"; not double-counted | | |
| A07 | Storage → hidden folders (e.g. `AppData`) | Shown with a dashed edge in the treemap and dimmed in the tree; the details panel says *Hidden* | | |
| A08 | Select an item → *Show in File Explorer* | Explorer opens with the item selected (also with spaces in the path) | | |
| A09 | Storage → *System drive* (C:\) | Starts, shows the folder being read, can be stopped with **Stop** or **Esc**; UI stays responsive | | |
| A10 | Scan the same folder twice (change something between) → *Compare scans* | Changes by file type and folder; warns if one scan was elevated or could read different amounts | | |
| A11 | High-DPI display (125%/150%) | Text is sharp, nothing is clipped; dark title bar | | |
| A12 | Settings → History on, wait ~10 minutes, ask *What changed recently?* | Compares the last 15 minutes with the hour before; `%LOCALAPPDATA%\OScope\history.db` exists | | |
| A13 | Settings → *Clear history* | Database content is gone; the app keeps working | | |
| A14 | Non-English Windows (if available) | Paging and GPU show **Unavailable** with the language note; everything else works | | |

## Step 4. Turning real output into test fixtures

Replace the hand-written samples in `tests/test_collectors.py` (typeperf CSV, PowerShell lines, registry values) with the real
captures from `--probe` (and from `typeperf ... -sc 1` / the PowerShell script's output), then run the suite. Anything the real
output breaks is exactly what this checklist is for.
