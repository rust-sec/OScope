"""Guards for promises the documentation makes: OScope is read-only, and the docs say how many tests there are."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Files that manage OScope's OWN data (settings, history database, reports) and may therefore create, replace or delete it.
OWN_DATA_FILES = {
    "app/history/settings_store.py",   # atomic settings write (temp file + replace)
    "app/history/db.py",               # sets a corrupt history database aside
    "app/core/report.py",              # writes reports
}
# Anything that could change the user's files, processes or system. None of it may appear in app/ outside OWN_DATA_FILES.
WRITE_OPERATIONS = re.compile(
    r"os\.remove|os\.unlink|\.unlink\(|os\.rmdir|\.rmdir\(|shutil\.rmtree|shutil\.move|shutil\.copy|os\.rename|os\.replace"
    r"|send2trash|taskkill|\.terminate\(|\.kill\(|os\.system|shell\s*=\s*True|SetFileAttributes|DeleteFile|MoveFile"
    r"|\.write_bytes\(|\.write_text\(|\.mkdir\(|open\([^)]*['\"][wax]"
)
# Folders OScope may create for itself (the app-data folder and its parents).
FOLDER_CREATION_ALLOWED = {"app/core/platform_ops.py"}


class ReadOnlyGuaranteeTests(unittest.TestCase):
    def sources(self):
        for path in sorted((ROOT / "app").rglob("*.py")):
            yield path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")

    def test_nothing_outside_oscopes_own_data_changes_files_processes_or_the_system(self):
        offenders = []
        for relative, text in self.sources():
            if relative in OWN_DATA_FILES or relative in FOLDER_CREATION_ALLOWED:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if line.lstrip().startswith("#"):
                    continue
                if WRITE_OPERATIONS.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "write-type operations outside OScope's own data files:\n" + "\n".join(offenders))

    def test_the_files_allowed_to_write_only_touch_oscopes_own_folder_names(self):
        # report, settings and history files all live under the app-data folder; make sure those modules do not
        # mention user-facing locations such as Documents, Downloads or Desktop as write targets.
        for relative in OWN_DATA_FILES | FOLDER_CREATION_ALLOWED:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for place in ("Documents", "Desktop", "Pictures"):
                self.assertNotIn(place, text, f"{relative} mentions {place}")

    def test_the_one_powershell_script_only_reads(self):
        from app.collectors.windows import thermal

        script = thermal._SCRIPT
        self.assertIn("Get-CimInstance", script)
        for verb in ("Remove-", "Set-", "Stop-", "Start-", "New-", "Invoke-", "Restart-", "Disable-", "Enable-", "Clear-",
                     "Add-", "Out-File", "Write-"):
            self.assertNotIn(verb, script, f"the PowerShell script uses {verb}")

    def test_helper_programs_are_only_started_by_the_command_runner(self):
        users = [relative for relative, text in self.sources() if re.search(r"subprocess\.(run|Popen|call|check_output)", text)]
        self.assertEqual(
            sorted(users),
            ["app/collectors/windows/cmd.py", "app/core/windows_backend.py"],
            "only the command runner (typeperf, PowerShell) and windows_backend (tasklist, explorer /select) may start programs",
        )

    def test_the_command_runner_never_uses_a_shell(self):
        text = (ROOT / "app/collectors/windows/cmd.py").read_text(encoding="utf-8")
        self.assertNotIn("shell=True", text)
        self.assertIn("timeout=timeout", text)

    def test_oscope_contains_no_network_code(self):
        # `import socket` alone is fine (socket.gethostname() asks the OS for this PC's name and sends nothing);
        # what must never appear is anything that opens a connection.
        network = re.compile(
            r"\b(import|from) (http|urllib|requests|ftplib|smtplib|xmlrpc|ssl|telnetlib|websockets|aiohttp)\b"
            r"|socket\.(socket|create_connection|getaddrinfo|gethostbyname)\(|\.connect_ex\("
        )
        offenders = [relative for relative, text in self.sources() if network.search(text)]
        self.assertEqual(offenders, [])


class DocumentationTests(unittest.TestCase):
    @staticmethod
    def _count_tests() -> int:
        return sum(
            len(re.findall(r"^\s+def test_", path.read_text(encoding="utf-8"), re.MULTILINE))
            for path in sorted((ROOT / "tests").glob("test_*.py"))
        )

    def test_the_docs_state_the_real_number_of_tests(self):
        actual = self._count_tests()
        for name in ("README.md", "TESTING.md", "PROJECT_SUMMARY.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            claimed = {int(n) for n in re.findall(r"\*\*(\d+) automated tests\*\*", text)}
            self.assertTrue(claimed, f"{name} should state the test count as **N automated tests**")
            self.assertEqual(claimed, {actual}, f"{name} says {sorted(claimed)}, but there are {actual} test methods")

    def test_the_docs_no_longer_make_claims_that_stopped_being_true(self):
        stale = {
            "README.md": ["ALL Windows-specific calls", "the ONLY file with Windows-specific calls", "No database"],
            "PROJECT_DOCUMENTATION.md": ["All Windows-specific calls in `windows_backend.py`", "No database, no config file"],
            "PANEL_QA.md": ["the Windows-specific parts (registry, `GlobalMemoryStatusEx`, `tasklist`) are in one file"],
            "PLAN.md": ["ALL Win32 / registry / `tasklist` calls"],
        }
        for name, phrases in stale.items():
            text = (ROOT / name).read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertNotIn(phrase, text, f"{name} still says: {phrase!r}")

    def test_new_documents_exist_and_are_linked_from_the_readme(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative in ("docs/EVIDENCE_AND_HONESTY.md", "docs/WINDOWS_VERIFICATION.md"):
            self.assertTrue((ROOT / relative).is_file(), relative)
            self.assertIn(relative, readme)

    def test_every_reading_the_ui_shows_has_a_line_in_the_verification_checklist(self):
        from app.collectors import labels

        checklist = (ROOT / "docs/WINDOWS_VERIFICATION.md").read_text(encoding="utf-8")
        for name, _title in labels.EVIDENCE_ROWS:
            self.assertIn(f"`{name}`", checklist, f"{name} is shown to users but not in docs/WINDOWS_VERIFICATION.md")


if __name__ == "__main__":
    unittest.main()
