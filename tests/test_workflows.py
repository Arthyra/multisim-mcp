import asyncio
from pathlib import Path
import tempfile
import unittest

from anyio import CancelScope
from mcp import types
from mcp.shared.exceptions import MCPError

from multisim_unified_mcp.f5_cases import verify_f5_cases
from multisim_unified_mcp.handoff import handoff_to_com
from multisim_unified_mcp.logic_cases import verify_logic_cases


class FakeBackend:
    def __init__(self, name, handler, tools=None):
        self.name = name
        self.stage = "ready"
        self.handler = handler
        self.tools = dict.fromkeys(tools or ())
        self.restrict_tools = tools is not None
        self.calls = []

    async def ping(self):
        return True

    def supports(self, name):
        return not self.restrict_tools or name in self.tools

    async def call_tool(self, name, arguments, **_):
        self.calls.append((name, arguments))
        data = await self.handler(name, arguments)
        return types.CallToolResult(content=[], structured_content=data)


class F5Test(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {"A": 0, "B": 1}
        self.initial_state = "stopped"
        self.actions = []
        self.clock = 0.0
        self.on_sample = None
        self.row = {"name": "10", "inputs": {"A": 1, "B": 0}, "expected": {"S": 1}}

    async def handler(self, name, args):
        if name == "native_interactive_status":
            return {"state_name": self.initial_state}
        if name == "native_control_interactive":
            self.actions.append(args["action"])
            return {"transition_confirmed": True}
        self.states.update(args["changes"])
        if not args["input_refs"] and self.on_sample:
            return await self.on_sample()
        self.clock += 0.01
        return {"inputs": self.states.copy(), "simulation_time_s": self.clock,
                "indicators": [{"ref": "S", "state": self.states["A"]}]}

    async def verify(self, **arguments):
        return await verify_f5_cases(FakeBackend("native", self.handler), {"cases": [self.row], **arguments})

    async def test_owned_run_and_mismatch(self):
        result = await self.verify()
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.states, {"A": 0, "B": 1})
        self.assertEqual(self.actions, ["start", "stop"])
        self.row["expected"]["S"] = 0
        result = await self.verify()
        self.assertFalse(result["passed"])
        self.assertEqual(result["cases"][0]["status"], "measured")

    async def test_timeout_and_running_state(self):
        self.initial_state = "running"

        async def stalled():
            return {"simulation_time_s": self.clock, "indicators": []}

        self.on_sample = stalled
        result = await self.verify(settle_timeout_s=0.01)
        self.assertEqual(result["cases"][0]["status"], "timeout")
        self.assertEqual(self.states, {"A": 0, "B": 1})
        self.assertEqual(self.actions, [])

    async def test_paused_session_untouched(self):
        self.initial_state = "paused"
        result = await self.verify()
        self.assertIn("paused", result["error"])
        self.assertEqual(self.actions, [])

    async def test_invalid_clock_and_missing_indicator(self):
        for sample in (
            {"simulation_time_s": float("nan"), "indicators": []},
            {"simulation_time_s": 10, "indicators": []},
            {"simulation_time_s": 10, "indicators": [{"ref": "S", "state": None}]},
        ):
            async def reply():
                return sample
            self.on_sample = reply
            result = await self.verify()
            self.assertFalse(result["passed"])
            self.assertEqual(self.states, {"A": 0, "B": 1})
            self.assertFalse(result["restore_errors"])

    async def test_backwards_clock_compares_to_previous_reading(self):
        stamps = iter([0.025, 0.024])

        async def backwards():
            return {"simulation_time_s": next(stamps), "indicators": []}

        self.on_sample = backwards
        result = await self.verify(settle_time_s=1)
        self.assertIn("backwards", result["error"])
        self.assertEqual(self.actions, ["start", "stop"])

    async def test_cancel_restores_switches_and_stops(self):
        with CancelScope() as request:
            async def interrupted():
                request.cancel()
                await asyncio.sleep(1)

            self.on_sample = interrupted
            await self.verify()
        self.assertTrue(request.cancelled_caught)
        self.assertEqual(self.states, {"A": 0, "B": 1})
        self.assertEqual(self.actions, ["start", "stop"])

    async def test_cancel_during_write_waits_before_restoring(self):
        calls = []
        handler = self.handler
        with CancelScope() as request:
            async def interrupted(name, args):
                if name == "native_f5_logic_snapshot" and args["changes"] == self.row["inputs"]:
                    request.cancel()
                    await asyncio.sleep(0.01)
                    calls.append("write_finished")
                if name == "native_f5_logic_snapshot" and args["changes"] == {"A": 0, "B": 1}:
                    calls.append("restore")
                return await handler(name, args)

            self.handler = interrupted
            await self.verify()
        self.assertTrue(request.cancelled_caught)
        self.assertEqual(calls, ["write_finished", "restore"])
        self.assertEqual(self.states, {"A": 0, "B": 1})
        self.assertEqual(self.actions, ["start", "stop"])

    async def test_disconnect_and_malformed_reply(self):
        async def disconnected():
            raise MCPError(code=types.CONNECTION_CLOSED, message="closed")

        self.on_sample = disconnected
        result = await self.verify()
        self.assertIn("closed", result["error"])
        self.assertEqual(self.states, {"A": 0, "B": 1})

        async def malformed():
            return {}

        self.on_sample = malformed
        result = await self.verify()
        self.assertIn("error", result)
        self.assertEqual(self.states, {"A": 0, "B": 1})

    async def test_permanent_disconnect_reports_failed_cleanup(self):
        handler = self.handler
        lost = False

        async def loses_connection(name, args):
            nonlocal lost
            if name == "native_f5_logic_snapshot" and not args["input_refs"]:
                lost = True
            if lost:
                raise MCPError(code=types.CONNECTION_CLOSED, message="Connection closed")
            return await handler(name, args)

        self.handler = loses_connection
        result = await self.verify()
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["restore_errors"]), 2)
        self.assertIn("restore inputs", result["restore_errors"][0])
        self.assertIn("stop F5", result["restore_errors"][1])


class HandoffTest(unittest.IsolatedAsyncioTestCase):
    async def test_stage_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.ms14"
            path.touch()
            source = str(path)
            stages = [
                ("validate_native", "native_handoff_context", {"isolated": True}),
                ("stop_native", "native_control_interactive", {"transition_confirmed": False}),
                ("save_native", "native_save_source", {"saved_to_source": False}),
                ("release_native", "native_close_copy", {"closed": False, "detached": False}),
                ("connect_com", "connect", {"connected": False}),
                ("open_com", "open_circuit", {"file": str(path.with_name("other.ms14"))}),
                ("open_com", "open_circuit", types.CONNECTION_CLOSED),
                ("save_native", "native_save_source", types.REQUEST_TIMEOUT),
            ]
            good = {
                "native_handoff_context": {"isolated": False, "source_path": source, "project_path": source},
                "native_control_interactive": {"transition_confirmed": True, "state_name": "stopped"},
                "native_save_source": {"saved_to_source": True, "path": source},
                "native_close_copy": {"closed": True},
                "connect": {"connected": True},
                "open_circuit": {"file": source},
            }
            for stage, failed_tool, failure in stages:
                with self.subTest(stage=stage, failure=failure):
                    async def handler(name, _):
                        if name != failed_tool:
                            return good[name]
                        if isinstance(failure, int):
                            raise MCPError(code=failure, message="simulated")
                        return failure

                    native = FakeBackend("native", handler)
                    com = FakeBackend("com", handler)
                    result = await handoff_to_com(native, com, {"path": source})
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["stage"], stage)
                    self.assertIn("recovery", result)
                    self.assertEqual(result["native_released"], stage in ("connect_com", "open_com"))
                    if stage == "save_native":
                        self.assertNotIn("native_close_copy", [name for name, _ in native.calls])

            async def success(name, _):
                return good[name]

            result = await handoff_to_com(FakeBackend("native", success), FakeBackend("com", success), {"path": source})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["saved_path"], result["opened_path"])

    async def test_cancellation_waits_for_save_and_does_not_release(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.ms14"
            path.touch()
            saved = []
            with CancelScope() as request:
                async def handler(name, _):
                    if name == "native_handoff_context":
                        return {"isolated": False, "source_path": str(path), "project_path": str(path)}
                    if name == "native_control_interactive":
                        return {"transition_confirmed": True}
                    self.assertEqual(name, "native_save_source")
                    request.cancel()
                    await asyncio.sleep(0.01)
                    saved.append(True)
                    return {"saved_to_source": True, "path": str(path)}

                native = FakeBackend("native", handler)
                com = FakeBackend("com", handler)
                await handoff_to_com(native, com, {"path": str(path)})
            self.assertTrue(request.cancelled_caught)
            self.assertEqual(saved, [True])
            self.assertNotIn("native_close_copy", [name for name, _ in native.calls])
            self.assertEqual(com.calls, [])


class ComTest(unittest.IsolatedAsyncioTestCase):
    async def test_backend_without_multi_output_declines_multi_case_before_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "case.ms14"
            path.touch()

            async def handler(name, _):
                if name == "open_circuit":
                    return {"file": str(path)}
                self.assertEqual(name, "run_transient")
                return {"ready": True, "rows": [[0, 1e-6], [0, 0.8]]}

            backend = FakeBackend("com", handler, ("run_transient",))
            case = {"name": "case", "path": str(path), "expected": {"L": 0}}
            result = await verify_logic_cases(backend, {"cases": [case]})
            self.assertTrue(result["passed"])
            backend.calls.clear()
            case["expected"]["H"] = 1
            result = await verify_logic_cases(backend, {"cases": [case]})
            self.assertIn("cannot sample multiple", result["cases"][0]["error"])
            self.assertEqual(backend.calls, [])

    async def test_synchronized_outputs_and_bad_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "case.ms14"
            path.touch()
            arguments = {"cases": [{"name": "case", "path": str(path), "expected": {"L": 0, "H": 1}}]}
            for high, final_time, expected_pass in (
                (2.0, 1e-6, True), (1.99, 1e-6, False), (float("inf"), 1e-6, False),
                (2.0, 0.7e-6, False), (2.0, 0.95e-6, False), (None, 1e-6, False),
            ):
                with self.subTest(high=high, final_time=final_time):
                    async def handler(name, _):
                        if name == "open_circuit":
                            return {"file": str(path)}
                        channels = {"L": {"rows": [[0, 1e-6], [0, 0.8]]}}
                        if high is not None:
                            channels["H"] = {"rows": [[0, final_time], [1, high]]}
                        return {"ready": True, "results": channels}

                    backend = FakeBackend("com", handler, ("run_transient_outputs",))
                    result = await verify_logic_cases(backend, arguments)
                    self.assertEqual(result["passed"], expected_pass, result)
                    self.assertEqual([name for name, _ in backend.calls], ["open_circuit", "run_transient_outputs"])
                    if expected_pass:
                        self.assertEqual(result["cases"][0]["outputs"]["L"]["observed"], 0)
