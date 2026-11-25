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


def render_bitmap(call, operation, args):
    region = args["region"]
    left, top, right, bottom = (region[key] for key in ("left", "top", "right", "bottom"))
    if not (left or top) or right <= 0 or bottom <= 0:
        return call(operation, args)
    bounds = call("circuitState")["content_bounds"]
    if bounds and ((left < 0 and bounds["left"] < 0) or (top < 0 and bounds["top"] < 0)):
        # Retain the upstream renderer for real objects left of or above the sheet origin.
        return call(operation, args)

    # Moving the drawing root for a crop leaves interactive switch faces behind.
    # Render at the original origin and crop the actual native pixels instead.
    origin_region = {**region, "left": 0, "top": 0}
    result = call(operation, {**args, "region": origin_region})
    if operation == "exportInteractiveImage" and not result["ready"]:
        return result

    from PIL import Image

    factor = args["render_scale"]
    width, height = round((right - left) * factor), round((bottom - top) * factor)
    offset_x, offset_y = round(left * factor), round(top * factor)
    path = Path(args["path"])
    with Image.open(path) as bitmap:
        cropped = Image.new("RGB", (width, height), "white")
        if bounds and not args["show_frame"] and not args["show_grid"]:
            # The renderer may still draw coordinate rulers with the frame disabled.
            # Preserve content, including its antialiased edge, on a clean canvas.
            x0 = max(0, round((bounds["left"] - 6) * factor))
            y0 = max(0, round((bounds["top"] - 6) * factor))
            x1 = min(bitmap.width, round((bounds["right"] + 6) * factor))
            y1 = min(bitmap.height, round((bounds["bottom"] + 6) * factor))
            if x1 > x0 and y1 > y0:
                cropped.paste(bitmap.crop((x0, y0, x1, y1)), (x0 - offset_x, y0 - offset_y))
        elif bounds or args["show_frame"] or args["show_grid"]:
            cropped.paste(bitmap, (-offset_x, -offset_y))
    cropped.save(path, format="BMP")
    return {**result, "region": region, "width": width, "height": height}


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

    upstream_call = server.host.call

    def call_editor(operation, args=None):
        if operation in ("exportImage", "exportInteractiveImage") and args and args.get("region"):
            return render_bitmap(upstream_call, operation, args)
        return upstream_call(operation, args)

    server.host.call = call_editor

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
