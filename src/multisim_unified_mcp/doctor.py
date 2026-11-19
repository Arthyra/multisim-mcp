"""Read-only diagnostics suitable for sharing in a public issue."""

import asyncio
import json
import os
import struct
import subprocess
import sys
import winreg

from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from .backend_calls import backend_result
from .backends import Backend, start_backends


PYTHON_PROBE = """
import importlib.util
import json
import struct
import sys

print(json.dumps({
    "bits": struct.calcsize("P") * 8,
    "version": list(sys.version_info[:3]),
    "implementation": sys.implementation.name,
    "missing_modules": [name for name in sys.argv[1:] if importlib.util.find_spec(name) is None],
}))
"""


def probe_python(backend: Backend) -> dict:
    modules = (
        ["multisim_native_mcp", "frida", "pythoncom"]
        if backend.name == "native" else ["multisim_mcp"]
    )
    try:
        params = backend.parameters()
    except ValidationError:
        return {"ok": False, "reason": "invalid_backend_configuration"}
    try:
        completed = subprocess.run(
            [params.command, "-c", PYTHON_PROBE, *modules],
            cwd=params.cwd, env=os.environ | (params.env or {}),
            stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return {"ok": False, "reason": "python_executable_not_found"}
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "reason": "python_probe_failed"}
    if completed.returncode:
        return {"ok": False, "reason": "python_probe_failed"}
    try:
        report = json.loads(completed.stdout)
    except ValueError:
        return {"ok": False, "reason": "python_probe_failed"}
    if backend.name == "native" and (
        report["bits"] != 32 or report["version"][:2] != [3, 12]
        or report["implementation"] != "cpython"
    ):
        report["reason"] = "native_requires_32_bit_cpython_3_12"
    elif report["missing_modules"]:
        report["reason"] = "missing_python_modules"
    report["ok"] = "reason" not in report
    return report


def com_registered() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, r"MultisimInterface.MultisimApp.1\CLSID", 0,
            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ):
            return True
    except OSError:
        return False


async def runtime_report(backend: Backend) -> dict:
    if backend.session is None:
        return {"ok": False, "reason": "backend_unavailable"}
    try:
        if backend.name == "native":
            runtime = await backend_result(backend, "native_runtime_status")
            return {
                "ok": runtime["available"] and runtime["compatible"],
                "multisim_installed": runtime["available"],
                "matches_tested_build": runtime["compatible"],
                "tested_build": runtime["tested_version"],
                "missing_modules": [
                    item["module"] for item in runtime["checks"] if not item["present"]
                ],
                "different_modules": [
                    item["module"] for item in runtime["checks"] if item["present"] and not item["match"]
                ],
            }
        runtime = await backend_result(backend, "runtime_status")
        registration = com_registered()
        return {
            "ok": runtime["runtime_compatible"] and registration,
            "com_registered": registration,
            "worker_python_bits": runtime["python_bits"],
            "pywin32_available": runtime["pywin32_available"],
            "worker_compatible": runtime["runtime_compatible"],
        }
    except (RuntimeError, MCPError):
        return {"ok": False, "reason": "runtime_probe_failed"}


async def diagnose(backends: dict[str, Backend]) -> dict:
    python_reports = {name: probe_python(backend) for name, backend in backends.items()}
    eligible = {name: backend for name, backend in backends.items() if python_reports[name]["ok"]}
    with open(os.devnull, "w") as stderr:
        async with start_backends(eligible, stderr):
            runtime_reports = await asyncio.gather(*(runtime_report(b) for b in backends.values()))
            reports = {
                name: {"python": python_reports[name], "startup": backend.status(), "runtime": runtime}
                for (name, backend), runtime in zip(backends.items(), runtime_reports)
            }
    gateway_ok = sys.platform == "win32" and struct.calcsize("P") == 8 and sys.version_info >= (3, 11)
    return {
        "ok": gateway_ok and all(
            report["python"]["ok"] and report["startup"]["available"] and report["runtime"]["ok"]
            for report in reports.values()
        ),
        "gateway": {
            "ok": gateway_ok, "platform": sys.platform,
            "python_bits": struct.calcsize("P") * 8, "python_version": list(sys.version_info[:3]),
        },
        "backends": reports,
    }
