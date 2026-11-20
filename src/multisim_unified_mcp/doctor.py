"""Read-only diagnostics suitable for sharing in a public issue."""

import asyncio
import json
import os
import re
import struct
import subprocess
import sys

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
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, r"MultisimInterface.MultisimApp.1\CLSID", 0,
            winreg.KEY_READ | winreg.KEY_WOW64_32KEY,
        ):
            return True
    except OSError:
        return False


async def native_f5_reason(backend: Backend) -> str | None:
    tools = (
        "native_interactive_status", "native_control_interactive", "native_logic_profile_status",
        "native_f5_logic_snapshot", "native_set_logic_input", "native_read_logic_indicators",
    )
    if not all(backend.supports(name) for name in tools):
        return "missing_f5_tools"
    try:
        compatible = (await backend_result(backend, "native_logic_profile_status")).get("compatible")
    except (RuntimeError, MCPError):
        return "f5_profile_probe_failed"
    if compatible is True:
        return None
    if compatible is False:
        return "f5_profile_incompatible"
    return "f5_profile_probe_failed"


async def native_report(backend: Backend) -> dict:
    try:
        if not backend.available():
            return {
                "ok": False, "reason": "backend_unavailable",
                "basic_ok": False, "basic_reason": "backend_unavailable",
                "f5_logic_compatible": False, "f5_reason": "native_basic_unavailable",
            }
        runtime = await backend_result(backend, "native_runtime_status")
        installed = runtime["available"] is True
        compatible = runtime["compatible"] is True
        basic_reason = None
        if not installed:
            basic_reason = "multisim_not_found"
        elif not compatible:
            basic_reason = "native_build_incompatible"
        f5_reason = "native_basic_unavailable" if basic_reason else await native_f5_reason(backend)
        return {
            "ok": basic_reason is None and f5_reason is None,
            "basic_ok": basic_reason is None,
            "basic_reason": basic_reason,
            "f5_logic_compatible": f5_reason is None,
            "f5_reason": f5_reason,
            "multisim_installed": installed,
            "matches_tested_build": compatible,
            "tested_build": runtime["tested_version"],
            "missing_modules": [item["module"] for item in runtime["checks"] if not item["present"]],
            "different_modules": [
                item["module"] for item in runtime["checks"] if item["present"] and not item["match"]
            ],
        }
    except (KeyError, TypeError, RuntimeError, MCPError):
        return {
            "ok": False, "reason": "runtime_probe_failed",
            "basic_ok": False, "basic_reason": "runtime_probe_failed",
            "f5_logic_compatible": False, "f5_reason": "native_basic_unavailable",
        }


async def runtime_report(backend: Backend) -> dict:
    if backend.name == "native":
        return await native_report(backend)
    if not backend.available():
        return {"ok": False, "reason": "backend_unavailable"}
    try:
        runtime = await backend_result(backend, "runtime_status")
        registration = com_registered()
        worker_ok = runtime["runtime_compatible"] is True
        reason = None
        if not worker_ok:
            reason = "com_worker_incompatible"
        elif not registration:
            reason = "com_not_registered"
        return {
            "ok": reason is None,
            "reason": reason,
            "com_registered": registration,
            "worker_python_bits": runtime["python_bits"],
            "pywin32_available": runtime["pywin32_available"],
            "worker_compatible": worker_ok,
            "synchronized_transient": backend.supports("run_transient_outputs"),
        }
    except (KeyError, TypeError, RuntimeError, MCPError):
        return {"ok": False, "reason": "runtime_probe_failed"}


async def diagnose(backends: dict[str, Backend]) -> dict:
    gateway_ok = sys.platform == "win32" and struct.calcsize("P") == 8 and sys.version_info >= (3, 11)
    gateway = {
        "ok": gateway_ok, "platform": sys.platform,
        "python_bits": struct.calcsize("P") * 8, "python_version": list(sys.version_info[:3]),
    }
    if sys.platform != "win32":
        return {"ok": False, "gateway": gateway, "reason": "windows_required", "backends": {}}
    python_reports = {name: probe_python(backend) for name, backend in backends.items()}
    eligible = {name: backend for name, backend in backends.items() if python_reports[name]["ok"]}
    with open(os.devnull, "w") as stderr:
        async with start_backends(eligible, stderr):
            runtime_reports = await asyncio.gather(*(runtime_report(b) for b in backends.values()))
            reports = {
                name: {"python": python_reports[name], "startup": backend.status(), "runtime": runtime}
                for (name, backend), runtime in zip(backends.items(), runtime_reports)
            }
    for report in reports.values():
        version = report["startup"]["version"]
        if version is not None and not re.fullmatch(r"v?\d+(?:\.\d+){1,3}", version):
            report["startup"]["version"] = None
    return {
        "ok": gateway_ok and reports["com"]["runtime"].get("synchronized_transient", False) and all(
            report["python"]["ok"] and report["startup"]["available"] and report["runtime"]["ok"]
            for report in reports.values()
        ),
        "gateway": gateway,
        "backends": reports,
    }
