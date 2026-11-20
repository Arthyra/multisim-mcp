import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from multisim_unified_mcp import doctor
from multisim_unified_mcp.backends import Backend


@asynccontextmanager
async def ready_backends(_backends, _stderr):
    yield


class DoctorTest(unittest.IsolatedAsyncioTestCase):
    async def report(self, *, build=True, f5=True, omit=None, registered=True):
        backends = {name: Mock(spec=Backend) for name in ("native", "com")}
        for name, backend in backends.items():
            backend.name = name
            backend.available.return_value = True
            backend.supports.side_effect = lambda tool: tool != omit
            backend.status.return_value = {"available": True, "version": "1.2.3"}

        async def reply(_backend, name):
            if name == "native_runtime_status":
                return {"available": True, "compatible": build, "tested_version": "14.3.1500.0", "checks": []}
            if name == "native_logic_profile_status":
                if not build:
                    self.fail("Do not probe the F5 extension on an incompatible base runtime")
                if f5 is None:
                    raise RuntimeError("probe failed")
                return {"compatible": f5}
            self.assertEqual(name, "runtime_status")
            return {"runtime_compatible": True, "python_bits": 32, "pywin32_available": True}

        with (
            patch.object(doctor, "sys", SimpleNamespace(platform="win32", version_info=(3, 13))),
            patch.object(doctor, "start_backends", ready_backends),
            patch.object(doctor, "probe_python", return_value={"ok": True}),
            patch.object(doctor, "com_registered", return_value=registered),
            patch.object(doctor, "backend_result", side_effect=reply),
        ):
            return await doctor.diagnose(backends)

    async def test_full_installation(self):
        report = await self.report()
        self.assertTrue(report["ok"])
        native = report["backends"]["native"]["runtime"]
        self.assertTrue(native["ok"])
        self.assertTrue(native["basic_ok"])
        self.assertTrue(native["f5_logic_compatible"])
        self.assertIsNone(native["f5_reason"])
        self.assertTrue(report["backends"]["com"]["runtime"]["ok"])

    async def test_f5_failure_does_not_hide_native_base_or_com(self):
        for f5, omit, reason in (
            (False, None, "f5_profile_incompatible"),
            (None, None, "f5_profile_probe_failed"),
            (True, "native_control_interactive", "missing_f5_tools"),
        ):
            with self.subTest(reason=reason):
                report = await self.report(f5=f5, omit=omit)
                self.assertFalse(report["ok"])
                native = report["backends"]["native"]["runtime"]
                self.assertFalse(native["ok"])
                self.assertTrue(native["basic_ok"])
                self.assertFalse(native["f5_logic_compatible"])
                self.assertEqual(native["f5_reason"], reason)
                self.assertTrue(report["backends"]["com"]["runtime"]["ok"])

    async def test_incompatible_native_build_cannot_pass(self):
        report = await self.report(build=False)
        self.assertFalse(report["ok"])
        native = report["backends"]["native"]["runtime"]
        self.assertFalse(native["basic_ok"])
        self.assertEqual(native["basic_reason"], "native_build_incompatible")
        self.assertFalse(native["f5_logic_compatible"])

    async def test_com_registration_and_partial_installation(self):
        unregistered = await self.report(registered=False)
        self.assertFalse(unregistered["ok"])
        self.assertEqual(unregistered["backends"]["com"]["runtime"]["reason"], "com_not_registered")
        partial = await self.report(omit="run_transient_outputs")
        self.assertFalse(partial["ok"])
        com = partial["backends"]["com"]["runtime"]
        self.assertTrue(com["ok"])
        self.assertFalse(com["synchronized_transient"])
