"""Run the gateway regression against local, user-supplied Multisim files."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters, stdio_client
from win32com.client import GetObject


TRUTH_TABLE = {
    "000": (0, 0), "001": (1, 0), "011": (0, 1), "010": (1, 0),
    "110": (0, 1), "111": (1, 1), "101": (0, 1), "100": (1, 0),
}
INPUTS = ("A", "B", "Cin")
INDICATORS = ("S", "Cout")
OUTPUTS = ("V(OutProbe)", "V(OutProbe1)")


async def call(session, tool, **arguments):
    result = await session.call_tool(tool, arguments)
    if result.is_error:
        error = "\n".join(item.text for item in result.content if item.type == "text")
        raise RuntimeError(f"{tool}: {error}")
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def simserver_running():
    processes = GetObject("winmgmts:").ExecQuery(
        "SELECT CommandLine FROM Win32_Process WHERE Name='multisim.exe'"
    )
    return any(
        re.search(r'(?:^|\s)"?/simserver(?=[:\s"]|$)', process.CommandLine or "", re.I)
        for process in processes
    )


async def ensure_simserver(session, zero_case):
    if not simserver_running():
        await call(session, "com_connect")
        try:
            await call(session, "com_open_circuit", path=str(zero_case))
            await call(session, "com_run_transient", output_name=OUTPUTS[0])
        finally:
            await call(session, "com_disconnect")
    if not simserver_running():
        raise RuntimeError("The startup regression requires a running Multisim /SimServer")


async def check_lamps(session):
    for bits, expected in TRUTH_TABLE.items():
        for ref, bit in zip(INPUTS, bits):
            reading = await call(session, "native_set_logic_input", component_ref=ref, state=int(bit))
            assert reading["state"] == int(bit), reading
        # The next graphics update is asynchronous. Adjacent cases differ by
        # one input and always change the sum, so a stale frame cannot pass.
        for _ in range(15):
            read = await call(session, "native_read_logic_indicators", component_refs=list(INDICATORS))
            observed = tuple(indicator["state"] for indicator in read["indicators"])
            if observed == expected:
                break
            await asyncio.sleep(0.08)
        assert observed == expected, (bits, expected, observed)
        print(f"F5 {bits} -> {observed}", flush=True)
    await call(session, "native_set_logic_input", component_ref="A", state=0)


async def verify_com(session, cases):
    outputs = (await call(session, "com_enum_outputs"))["outputs"]
    assert set(OUTPUTS) <= set(outputs), outputs
    for output in OUTPUTS:
        wave = await call(
            session, "com_run_transient", output_name=output, duration=1e-6,
            sample_rate=1e7, num_samples=10, max_points=10,
        )
        assert wave["ready"] and wave["rows"][1][-1] == 0.0, wave
    batch = [
        {"name": bits, "path": str(cases[bits]), "expected": dict(zip(OUTPUTS, expected))}
        for bits, expected in TRUTH_TABLE.items()
    ]
    result = await call(session, "com_verify_logic_cases", cases=batch)
    assert result["passed"] and result["passed_count"] == 8, result
    wrong = {"name": "wrong-000", "path": str(cases["000"]), "expected": {OUTPUTS[0]: 1}}
    result = await call(session, "com_verify_logic_cases", cases=[wrong])
    assert not result["passed"], result
    assert result["cases"][0]["outputs"][OUTPUTS[0]]["observed"] == 0, result
    print("COM measured the saved handoff file, passed 8/8 cases and rejected a false expectation.", flush=True)


async def run(config, adder, cases_dir):
    gateway = StdioServerParameters(
        command=str(Path(sys.executable).with_name("multisim-unified-mcp.exe")),
        args=["--config", str(config.resolve(strict=True))],
    )
    with tempfile.TemporaryDirectory(prefix="multisim-regression-") as temporary:
        workspace = Path(temporary)
        project = workspace / "AdderRegression.ms14"
        shutil.copy2(adder, project)
        cases = {bits: workspace / f"E5_{bits}.ms14" for bits in TRUTH_TABLE}
        for destination in cases.values():
            shutil.copy2(cases_dir / destination.name, destination)

        async with stdio_client(gateway) as streams, ClientSession(*streams, read_timeout_seconds=180) as session:
            await session.initialize()
            backends = await call(session, "gateway_status")
            assert all(backends[name]["reachable"] for name in ("native", "com")), backends
            runtime = await call(session, "native_runtime_status")
            if runtime["processes"]:
                raise RuntimeError("Close schematic editor windows before the isolated startup test")
            await ensure_simserver(session, cases["000"])
            opened = await call(session, "native_open_circuit", source_path=str(project))
            assert Path(opened["source_path"]) == project and not opened.get("attached_existing"), opened
            print("Opened a dedicated editor while /SimServer was running.", flush=True)

            library = await call(session, "native_search_library", query="74LS153N", limit=5)
            assert any(
                part["Component_Name"] == "74LS153N" and part["Family_Name"] == "74LS"
                and part["Group_Name"] == "TTL" for part in library["results"]
            ), library
            chip = await call(
                session, "native_place_component", name="74LS153N", group="TTL", family="74LS",
                reference="U3", x=850, y=650,
            )
            assert chip["component"]["ref"] == "U3", chip
            assert {"1Y", "2Y"} <= {pin["name"] for pin in chip["component"]["pins"]}, chip
            await call(session, "native_delete_object", component_ref="U3")
            print("Placed a native 74LS153N from the master library.", flush=True)

            started = await call(session, "native_control_interactive", action="start", wait_timeout=10)
            assert started["transition_confirmed"], started
            try:
                wrong = await call(session, "handoff_to_com", path=str(cases["000"]))
                assert not wrong["ok"] and wrong["stage"] == "validate_native", wrong
                assert wrong["saved_path"] is None and not wrong["native_released"], wrong
                print("Rejected a handoff for a different .ms14.", flush=True)
                await check_lamps(session)
                receipt = await call(session, "handoff_to_com", path=str(project))
                assert receipt["ok"] and receipt["stage"] == "complete", receipt
                assert receipt["native_released"] and Path(receipt["saved_path"]) == project, receipt
                assert Path(receipt["opened_path"]) == project, receipt
                print("Stopped F5, saved and opened the exact same file in COM.", flush=True)
            except Exception:
                # If handoff has not released the session, stop the scratch simulation.
                status = await call(session, "native_runtime_status")
                if status["session_attached"]:
                    await call(session, "native_control_interactive", action="stop", wait_timeout=10)
                    await call(session, "native_close_copy", save_changes=False)
                raise
            try:
                await verify_com(session, cases)
            finally:
                await call(session, "com_disconnect")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--adder", type=Path, required=True, help="Full-adder .ms14 with F5 inputs")
    parser.add_argument("--cases-dir", type=Path, required=True, help="Directory with E5_000.ms14 through E5_111.ms14")
    args = parser.parse_args()
    asyncio.run(run(args.config, args.adder, args.cases_dir))
