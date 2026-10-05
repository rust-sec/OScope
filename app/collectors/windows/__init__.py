"""Windows evidence collectors.

Every function that talks to Windows here takes an injectable shim (a command
runner, a ``winreg`` module, a ctypes-backed function) so the parsing and the
availability logic can be unit-tested on any OS. What the real Windows returns
is verified by hand: see docs/WINDOWS_VERIFICATION.md and ``python main.py --probe``.
"""
