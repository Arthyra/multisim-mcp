"""Launch the pinned native backend with editor-only process discovery."""

from __future__ import annotations

from functools import cache
from pathlib import Path
import re
from typing import Literal


NATIVE_VERSION = "0.5.2"
INTERACT_SHA256 = "68a99100ed189486f72fbe06c9aa92ab21b20f8ce0d72d3c51b9aa2093fca3f6"
SIM_SERVER = re.compile(r'(?:^|\s)"?/simserver(?=[:\s"]|$)', re.IGNORECASE)


def multisim_processes() -> dict[int, tuple[str | None, str | None]]:
    import pythoncom
    from win32com.client import GetObject

    pythoncom.CoInitialize()
    try:
        rows = GetObject("winmgmts:").ExecQuery(
            "SELECT ProcessId, CommandLine, ExecutablePath FROM Win32_Process WHERE Name='multisim.exe'"
        )
        try:
            return {row.ProcessId: (row.CommandLine, row.ExecutablePath) for row in rows}
        finally:
            del rows
    finally:
        pythoncom.CoUninitialize()


class EditorDevice:
    def __init__(self, device):
        self.device = device

    def enumerate_processes(self):
        excluded = {pid for pid, (command, _) in multisim_processes().items() if SIM_SERVER.search(command or "")}
        return [process for process in self.device.enumerate_processes() if process.pid not in excluded]


def require_native_profile(runtime_status):
    status = runtime_status()
    if not (status["available"] and status["compatible"]):
        raise RuntimeError("Native requires the verified Multisim 14.3.1500.0 module fingerprints")


class EditorFrida:
    def __init__(self, frida, install, runtime_status):
        self.frida = frida
        self.executable = (install / "multisim.exe").resolve()
        self.runtime_status = runtime_status

    def get_local_device(self):
        return EditorDevice(self.frida.get_local_device())

    def attach(self, pid):
        _, image = multisim_processes().get(pid, (None, None))
        if not image or Path(image).resolve() != self.executable:
            raise RuntimeError("Native cannot verify that the selected process uses the configured Multisim executable")
        require_native_profile(self.runtime_status)
        return self.frida.attach(pid)

    def __getattr__(self, name):
        return getattr(self.frida, name)


def main() -> None:
    import frida
    from multisim_native_mcp import __version__
    from multisim_native_mcp import bridge, server, service

    if __version__ != NATIVE_VERSION:
        raise RuntimeError(f"The native worker requires multisim-native-mcp {NATIVE_VERSION}")

    upstream_status = server.host.runtime_status

    def runtime_status():
        return {**upstream_status(), "profile_enforced": True}

    server.host.runtime_status = runtime_status
    # Both upstream attachment paths use this guard before loading private code.
    bridge.frida = service.frida = EditorFrida(frida, bridge.INSTALL, runtime_status)

    upstream_open = bridge.NativeHost._open

    def open_editor(host, source_path, isolated=False, output_path=None, _startup_attempt=0):
        require_native_profile(runtime_status)
        try:
            return upstream_open(host, source_path, isolated, output_path, _startup_attempt)
        except RuntimeError as error:
            # On some cold launches the x86 Frida agent exits before the
            # circuit is editable. Retry only the creation of an owned editor.
            if isolated or _startup_attempt or str(error) != "Multisim exited during startup with code 3221225477":
                raise
            return upstream_open(host, source_path, _startup_attempt=1)

    bridge.NativeHost._open = open_editor

    upstream_create = server.host.create

    def create_editor(title="Circuit", output_path=None):
        require_native_profile(runtime_status)
        return upstream_create(title, output_path)

    server.host.create = create_editor

    upstream_script = bridge.editor_script
    logic_script = Path(__file__).with_name("interactive_logic.js").read_text(encoding="utf-8")

    def editor_script():
        logic_profile_matches.cache_clear()
        return upstream_script() + "\n" + logic_script

    bridge.editor_script = service.editor_script = editor_script

    @server.server.tool()
    @server.checked
    def native_handoff_context() -> dict:
        """Read the current document identity for a gateway handoff."""
        host = server.host
        return {**host.document_context(), "project_path": host.call("inspect")["projectPath"]}

    @cache
    def logic_profile_matches():
        try:
            return (server.host.runtime_status()["compatible"]
                    and bridge.sha256(bridge.INSTALL / "interact.dll") == INTERACT_SHA256)
        except OSError:
            return False

    def require_logic_profile():
        if not logic_profile_matches():
            raise RuntimeError("Logic I/O requires the tested Multisim 14.3.1500.0 runtime and interact.dll")

    @server.server.tool()
    @server.checked
    def native_logic_profile_status() -> dict:
        """Check the pinned interactive-logic build profile without opening a document."""
        return {"compatible": logic_profile_matches()}

    @server.server.tool()
    @server.checked
    def native_set_logic_input(component_ref: str, state: Literal[0, 1]) -> dict:
        """Set a stock INTERACTIVE_DIGITAL_CONSTANT during a running F5 simulation."""
        require_logic_profile()
        return server.host.call("setLogicInput", {"ref": component_ref, "state": state})

    @server.server.tool()
    @server.checked
    def native_read_logic_indicators(component_refs: list[str]) -> dict:
        """Read the actual illuminated state of stock PROBE_DIG_RED indicators in F5."""
        require_logic_profile()
        return server.host.call("readLogicIndicators", {"refs": component_refs})

    @server.server.tool()
    @server.checked
    def native_f5_logic_snapshot(
        input_refs: list[str], changes: dict[str, Literal[0, 1]], indicator_refs: list[str],
    ) -> dict:
        """Read or set F5 logic inputs and take a same-dispatch clock and output snapshot."""
        require_logic_profile()
        return server.host.call("f5LogicSnapshot", {
            "input_refs": input_refs, "changes": changes, "indicator_refs": indicator_refs,
        })

    server.main()


if __name__ == "__main__":
    main()
