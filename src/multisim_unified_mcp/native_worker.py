"""Launch the pinned native backend with editor-only process discovery."""

from __future__ import annotations

from functools import cache
from pathlib import Path
import re
from typing import Literal


NATIVE_VERSION = "0.5.2"
INTERACT_SHA256 = "68a99100ed189486f72fbe06c9aa92ab21b20f8ce0d72d3c51b9aa2093fca3f6"
SIM_SERVER = re.compile(r'(?:^|\s)"?/simserver(?=[:\s"]|$)', re.IGNORECASE)


def reuse_editor_views(script):
    """Make the pinned agent reuse views for single-sheet schematics."""
    activate = "'mscdecl')(treeInstance(),0,1);"
    if script.count(activate) != 1:
        raise RuntimeError("The pinned native editor's sheet activation code has changed")
    # The native reuse flag worked on single-sheet projects. During multi-sheet
    # save verification it can crash the editor, so keep that workflow unchanged.
    return script.replace(activate, "'mscdecl')(treeInstance(),"
                          "diagram().candidates.filter(s=>/CSCH_CAPSubSheet/.test(s.name)).length===1?1:0,1);")


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


def distant_power_labels(components, wires, page, intersects):
    """Warn about power labels more than three eighths of an inch from their symbols."""
    issues = []
    segments = [(a, b) for wire in wires for a, b in zip(wire["points"], wire["points"][1:])]
    for part in components:
        if not part.get("power_symbol"):
            continue
        body = part["body_bounds"]
        for label in part["labels"]:
            if not label["text"] or not label.get("bounds"):
                continue
            box = label["bounds"]
            dx = max(body["left"] - box["right"], box["left"] - body["right"], 0)
            dy = max(body["top"] - box["bottom"], box["top"] - body["bottom"], 0)
            if dx * dx + dy * dy <= 36 * 36:
                continue

            issue = {"kind": "distant_power_label", "severity": "warning", "component": part["ref"],
                     "label_kind": label["kind"], "label_id": label["id"], "label_text": label["text"],
                     "distance": round((dx * dx + dy * dy) ** .5, 1),
                     "message": "The power label is far from its symbol (distance in sheet units)."}
            obstacles = [p["body_bounds"] for p in components]
            obstacles += [l["bounds"] for p in components for l in p.get("labels", ())
                          if l["id"] != label["id"] and l["text"] and l.get("bounds")]
            half_width = (box["right"] - box["left"]) / 2
            half_height = (box["bottom"] - box["top"]) / 2
            mid_x = (body["left"] + body["right"]) / 2
            mid_y = (body["top"] + body["bottom"]) / 2
            candidates = ((body["right"] + 6 + half_width, mid_y), (body["left"] - 6 - half_width, mid_y),
                          (mid_x, body["bottom"] + 6 + half_height), (mid_x, body["top"] - 6 - half_height))
            for x, y in candidates:
                x, y = round(x), round(y)
                bounds = {"left": x - half_width, "right": x + half_width,
                          "top": y - half_height, "bottom": y + half_height}
                if not (page["left"] <= bounds["left"] <= bounds["right"] <= page["right"] and
                        page["top"] <= bounds["top"] <= bounds["bottom"] <= page["bottom"]):
                    continue
                if any(max(r["left"], bounds["left"]) < min(r["right"], bounds["right"]) and
                       max(r["top"], bounds["top"]) < min(r["bottom"], bounds["bottom"]) for r in obstacles):
                    continue
                if any(intersects(a, b, bounds) for a, b in segments):
                    continue
                issue["suggestion"] = "move_component_label"
                issue["suggested_arguments"] = {"component_ref": part["id"], "kind": label["kind"], "x": x, "y": y}
                break
            issues.append(issue)
    return issues


def missing_global_supplies(components, netlist_statements, diagnostics):
    """Report global rail names found on hidden pins but absent outside logic bridges."""
    rails = {"VCC", "GND", "VDD", "VSS", "VEE"}
    already_reported = {d.get("node", "").upper() for d in diagnostics}
    circuit_nodes = set()
    for statement in netlist_statements:
        body = [token.upper() for token in statement[1:]]
        if (statement[0].upper().startswith("X") and
                any(re.match(r"(?:TTL|CMOS)\w*(?:RCV|DRV)(?:_|$)", token) for token in body)):
            continue
        circuit_nodes.update(body)

    missing = {}
    for part in components:
        for pin in part.get("hidden_power_pins", ()):
            node = (pin["net"] or "").upper()
            if node in rails and node not in circuit_nodes and node not in already_reported:
                missing.setdefault(node, []).append({"ref": part["ref"], "pin": pin["number"], "name": pin["name"]})
    return [{"kind": "hidden_power_rail", "severity": "warning", "node": node, "components": parts,
             "reason": "missing_connection", "suggestion": "connect_hidden_power_rail",
             "message": f"Hidden power pins use {node}, but it has no external connection in the native netlist."}
            for node, parts in sorted(missing.items())]


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
    from multisim_unified_mcp.layout_workflow import (
        apply_reviewed_layout, layout_identity, placement_operations, register_layout_plan,
    )

    layout_plans = {}

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
    diagnostics_script = Path(__file__).with_name("schematic_diagnostics.js").read_text(encoding="utf-8")

    def editor_script():
        logic_profile_matches.cache_clear()
        source = reuse_editor_views(upstream_script())
        label_function = "function straightenComponentLabels(ref) {"
        if source.count(label_function) != 1:
            raise RuntimeError("The pinned native editor's label orientation code has changed")
        source = source.replace(label_function, label_function + "\n  uprightReflections(ref);")
        return "\n".join((source, logic_script, diagnostics_script))

    bridge.editor_script = service.editor_script = editor_script
    from multisim_native_mcp import edit_contracts

    service.MUTATIONS.update(("uprightLabels", "moveForPlacement"))
    edit_contracts.OPERATIONS["move_component_preserving_wiring"] = "moveForPlacement"
    edit_contracts.COMPONENT_OPERATIONS.add("moveForPlacement")

    def power_netlist(include_parameters=False):
        from multisim_native_mcp.netlist_diagnostics import statements

        result = server.host.netlist()
        state = server.host.call("circuitStateWithPower", {"include_parameters": include_parameters})
        if not result.get("failed"):
            result["diagnostics"].extend(missing_global_supplies(
                state["components"], statements(result["content"]), result["diagnostics"],
            ))
        return result, state

    server.server.remove_tool("native_get_circuit_state")

    @server.server.tool()
    @server.checked
    def native_get_circuit_state(include_parameters: bool = False, check_power: bool = False) -> dict:
        """Read selected-sheet geometry, components, wires, and hidden power pins with native node names. check_power=true runs native netlist diagnostics for missing global supplies and requires F5 to be stopped. Coordinates are sheet units (96 per inch)."""
        netlist = None
        if check_power:
            with server.host.lock:
                netlist, state = power_netlist(include_parameters)
        else:
            state = server.host.call("circuitStateWithPower", {"include_parameters": include_parameters})
        missing = [{"ref": part["ref"], **pin} for part in state["components"]
                   for pin in part.get("hidden_power_pins", ()) if pin["net"] is None]
        if missing:
            state["unassigned_hidden_power_pins"] = missing
        if netlist is not None:
            state["power_check"] = {"failed": bool(netlist.get("failed")), "diagnostics": netlist["diagnostics"]}
            for key in ("handles_invalidated", "handle_generation", "id_map", "unresolved_ids"):
                if key in netlist:
                    state[key] = netlist[key]
        return state

    server.server.remove_tool("native_check_netlist")

    @server.server.tool()
    @server.checked
    def native_check_netlist() -> dict:
        """Generate native SPICE and report missing or floating global supplies, including dependencies obtained from hidden pin mappings. Regenerating after topology changes can invalidate handles; use the returned state and id_map."""
        with server.host.lock:
            result, _ = power_netlist()
        return result

    server.server.remove_tool("native_check_layout")

    @server.server.tool()
    @server.checked
    def native_check_layout() -> dict:
        """Check endpoints, collisions, minimum symbol clearance, distant power labels and collinear wire overlaps. Symbol clearance includes wires along the body of their own attached component. Use native_simplify_branches for shared routes and separate distinct overlapping routes."""
        from multisim_native_mcp.layout import check_layout, intersects
        from multisim_unified_mcp.wiring import label_move_hints, wire_body_clearance_issues, wire_overlaps

        data = server.host.call("layoutWithPowerSymbols")
        result = check_layout(data["components"], data["wires"])
        warnings = distant_power_labels(data["components"], data["wires"], data["page"], intersects)
        warnings.extend(wire_overlaps(data["wires"]))
        crossings = {(issue['wire'], issue['component']) for issue in result['issues']
                     if issue['kind'] == 'wire_crosses_component'}
        warnings.extend(issue for issue in wire_body_clearance_issues(data['components'], data['wires'], intersects)
                        if (issue['wire'], issue['component']) not in crossings)
        result["issues"].extend(warnings)
        result["warnings"] += len(warnings)
        result["passed"] = not result["issues"]
        result["label_move_hints"] = label_move_hints(
            {**data, "page": {"bounds": data["page"]}}, result["issues"], intersects
        )
        return result

    @server.server.tool()
    @server.checked
    def native_simplify_branches(wire_ids: list[str] | None = None, apply: bool = False) -> dict:
        """Preview factoring same-direction fan-out segments into native junctions and one trunk. Skip user-named wires, off-grid junctions, and points occupied by another connector network. apply=true performs one undoable edit, verifies visible terminal groups, and does not save."""
        from multisim_unified_mcp.wiring import plan_shared_trunks, terminal_groups

        with server.host.lock:
            state = server.host.call("branchState")
            requested = None if wire_ids is None else set(wire_ids)
            if requested is not None:
                missing = requested - {wire["id"] for wire in state["wires"]}
                if missing:
                    raise ValueError(f"Unknown wire IDs: {', '.join(sorted(missing))}")
            changes, operations = plan_shared_trunks(state, requested)
            applied = bool(apply and operations)
            if applied:
                server.host.edit_sequence([*operations, {"operation": "repair_wire_geometry"}], {})
                after = server.host.call("circuitState")
                if terminal_groups(state["wires"]) != terminal_groups(after["wires"]):
                    recovery = server.host.history("undo")
                    raise service.NativeEditError("Branch edit changed native terminal groups and was undone", recovery)
        return {"applied": applied, "changes": changes, "length_removed": sum(c["length_removed"] for c in changes),
                "bends_removed": sum(c["bends_removed"] for c in changes)}

    @server.server.tool()
    @server.checked
    def native_upright_labels(component_refs: list[str]) -> dict:
        """Correct reflected reference and value text without moving components, pins or wires. Existing label centers are kept. The change is undoable."""
        if not component_refs:
            return {"changed": []}
        result = server.host.batch([{"operation": "uprightLabels", "arguments": {"refs": component_refs}}])
        return result["results"][0]

    @server.server.tool()
    @server.checked
    def native_plan_parallel_trunk(load_pins: dict[str, str], trunk_y: int | None = None,
                                   reuse_existing: bool = False, apply: bool = False) -> dict:
        """Preview same-net vertical taps. reuse_existing replaces the selected single-feed branch with drops from an existing same-net span. Optional trunk_y selects the 9-unit sheet coordinate. With apply=true, apply the chosen plan in one undoable edit, verify native terminal groups, and leave saving to the caller."""
        from multisim_native_mcp.layout import intersects
        from multisim_unified_mcp.wiring import plan_parallel_trunk, terminal_groups

        host = server.host
        with host.lock:
            state = host.call("branchState")
            plan = plan_parallel_trunk(state, load_pins, intersects, trunk_y, reuse_existing)
            if not apply or "reason" in plan:
                return plan
            operations = plan.get("operations")
            if operations is None:
                operations = [{"operation":"move_junction", "arguments": move} for move in plan["junction_moves"]]
                operations.extend({"operation":"route_wire", "arguments": route} for route in plan["routes"])
            host.edit_sequence([*operations, {"operation":"repair_wire_geometry"}], {})
            if terminal_groups(state["wires"]) != terminal_groups(host.call("circuitState")["wires"]):
                recovery = host.history("undo")
                raise service.NativeEditError("Trunk edit changed native terminal groups and was undone", recovery)
            return {**plan, "applied": True}

    @server.server.tool()
    @server.checked
    def native_simplify_wiring(wire_ids: list[str] | None = None, apply: bool = False,
                               placement_refs: list[str] | None = None, include_obstacle_routes: bool = False) -> dict:
        """Preview clearer orthogonal routes with at least one grid step of symbol clearance except outward pin leads. Then prefer less overlap, fewer point contacts and bends, and shorter length. Preserve native endpoints and pin approach directions. Use include_obstacle_routes with explicit wire_ids to compare upstream routes. apply=true edits routes in one undoable batch."""
        from multisim_native_mcp.layout import intersects, plan_wire
        from multisim_unified_mcp.wiring import pin_alignment_hints, plan_shorter_wires

        with server.host.lock:
            state = server.host.call("circuitState")
            requested = None if wire_ids is None else set(wire_ids)
            if requested is not None:
                missing = requested - {wire["id"] for wire in state["wires"]}
                if missing:
                    raise ValueError(f"Unknown wire IDs: {', '.join(sorted(missing))}")
            extra_routes = {}
            if include_obstacle_routes:
                if not requested:
                    raise ValueError("Specify wire_ids when comparing obstacle routes")
                for wire in state["wires"]:
                    if wire["id"] not in requested:
                        continue
                    try:
                        points = plan_wire(state["components"], wire)["points"]
                    except ValueError:
                        continue
                    extra_routes[wire["id"]] = [[(point["x"], point["y"]) for point in points]]
            changes = plan_shorter_wires(state, intersects, requested, extra_routes)
            placement = pin_alignment_hints(state, set(placement_refs)) if placement_refs else None
            applied = bool(apply and changes)
            if applied:
                server.host.batch([
                    {"operation": "route_wire", "arguments": {"wire_id": change["wire_id"], "points": change["points"]}}
                    for change in changes
                ], atomic=True)
        result = {"applied": applied, "changes": changes,
                  "bends_removed": sum(change["bends_before"] - change["bends_after"] for change in changes)}
        if placement is not None:
            result["alignment_hints"] = placement
        return result

    @server.server.tool()
    @server.checked
    def native_plan_local_routing(wire_ids: list[str] | None = None, component_refs: list[str] | None = None,
                                  apply: bool = False) -> dict:
        """Compare combinations for 2–16 nearby wires, with all pins and junctions fixed. Select explicit wire_ids or the wires incident on component_refs. Returns up to three improvements with local intersection, clearance, bend and length totals. apply=true applies the best combination as an undoable batch without saving."""
        from multisim_native_mcp.layout import intersects
        from multisim_unified_mcp.wiring import plan_local_routing, terminal_groups

        if bool(wire_ids) == bool(component_refs):
            raise ValueError("Select wire_ids or component_refs")
        host = server.host
        with host.lock:
            state = host.call("circuitState")
            if component_refs:
                requested = set(component_refs)
                unknown = requested - {part['ref'] for part in state['components']}
                if unknown:
                    raise ValueError(f"Unknown component references: {', '.join(sorted(unknown))}")
                selected = {wire['id'] for wire in state['wires'] if any(
                    wire[key].get('ref') in requested for key in ('source', 'target'))}
            else:
                selected = set(wire_ids or ())
            result = plan_local_routing(state, intersects, selected)
            if not apply or not result['alternatives']:
                return {**result, 'applied': False}
            host.batch([{'operation':'route_wire', 'arguments':change}
                        for change in result['alternatives'][0]['changes']], atomic=True)
            if terminal_groups(state['wires']) != terminal_groups(host.call('circuitState')['wires']):
                recovery = host.history('undo')
                raise service.NativeEditError('Local routing changed native terminal groups and was undone', recovery)
            return {**result, 'applied': True}

    @server.server.tool()
    @server.checked
    def native_preview_ordered_wiring(pairs: list[tuple[tuple[str,str], tuple[str,str]]],
                                      reserved_columns: list[int] | None = None,
                                      supply_load_pins: dict[str,str] | None = None) -> dict:
        """Preview 2–8 ordered horizontal pin pairs using separate vertical channels. Each pair is [[source_ref,pin],[target_ref,pin]]. Reserve columns for adjacent signals. Free pin pairs are connected, existing direct wires are rerouted. Optionally merge selected supply loads onto an existing same-net trunk in the same plan. Use native_apply_layout_plan to apply the returned plan_id."""
        from multisim_native_mcp.layout import intersects
        from multisim_unified_mcp.wiring import plan_ordered_wiring, plan_parallel_trunk, terminal_groups

        host = server.host
        with host.lock:
            state = host.call('circuitState')
            ordered = plan_ordered_wiring(state,pairs,intersects,reserved_columns or ())
            if 'reason' in ordered:
                return ordered
            supply = None
            operations = ordered.pop('operations')
            if supply_load_pins:
                supply = plan_parallel_trunk(host.call('branchState'),supply_load_pins,intersects,reuse_existing=True)
                if 'reason' in supply:
                    return {**ordered,'supply':supply,'reason':'Supply trunk has no feasible plan'}
                operations.extend(supply.pop('operations'))
            identity = layout_identity(host,state)
            expected = terminal_groups(state['wires'] + ordered.pop('new_links'))
            ident = register_layout_plan(layout_plans,identity,operations,expected)
            return {**ordered,'supply':supply,'plan_id':ident}

    @server.server.tool()
    @server.checked
    def native_apply_layout_plan(plan_id: str, image_prefix: str | None = None,
                                 region: dict[str,int] | None = None) -> dict:
        """Apply a reviewed wiring or placement plan as one undoable edit. Reject a stale document. Undo if terminal groups or layout checks fail. An absolute image_prefix exports before/after PNGs with the same region and scale. Does not save the circuit."""
        from multisim_native_mcp.layout import check_layout, intersects

        host = server.host
        with host.lock:
            if plan_id not in layout_plans:
                raise ValueError('Unknown or already applied layout plan. Generate a fresh preview.')
            result = apply_reviewed_layout(host,layout_plans[plan_id],check_layout,intersects,
                                           service.NativeEditError,image_prefix,region)
            layout_plans.pop(plan_id)
            return result

    @server.server.tool()
    @server.checked
    def native_plan_local_placement(movable_refs: list[str], movable_junction_ids: list[str] | None = None,
                                    wire_ids: list[str] | None = None,
                                    candidates: dict[str, dict[str, tuple[int,int]]] | None = None) -> dict:
        """Preview joint placement and routing for 1–3 movable parts. Wires attached to them or unlocked junctions form the scope; wire_ids can add stationary-end wires. Compare with fixed placement. Returned candidate plan IDs can be applied with native_apply_layout_plan."""
        from multisim_native_mcp.layout import intersects, orthogonal_path
        from multisim_unified_mcp.wiring import plan_local_placement, terminal_groups

        host = server.host
        with host.lock:
            state = host.call('circuitState')
            result = plan_local_placement(state,intersects,orthogonal_path,movable_refs,movable_junction_ids or (),
                                          wire_ids or (),candidates)
            identity = layout_identity(host,state)
            expected = terminal_groups(state['wires'])
            for candidate in [result['fixed'],*result['candidates']]:
                if 'routes' not in candidate:
                    continue
                operations = placement_operations(state, {**candidate, 'moves': candidate.get('moves', {})})
                candidate['plan_id'] = register_layout_plan(layout_plans,identity,operations,expected)
            return result

    @server.server.tool()
    @server.checked
    def native_plan_sheet_layout(blocks: dict[str, list[str]] | None = None, fixed_refs: list[str] | None = None,
                                 canvas: tuple[int, int] | None = None,
                                 supply_load_pins: dict[str, str] | None = None) -> dict:
        """Preview whole-sheet placement and wiring on a working copy. Supply functional blocks, or omit blocks to infer aligned connected groups. Compare block spacing, terminal alignment, and joint routing of competing wires. Fixed refs keep their positions. Canvas is optionally [width,height]. Supply load pins can share an existing same-net rail. Review and apply the returned plan_id with native_apply_layout_plan."""
        from multisim_native_mcp.layout import check_layout, intersects, orthogonal_path
        from multisim_unified_mcp.sheet_layout import infer_layout_blocks, plan_sheet_layout
        from multisim_unified_mcp.wiring import plan_parallel_trunk, preview_route_state, terminal_groups

        host = server.host
        with host.lock:
            state = host.call('circuitState')
            inferred = blocks is None
            if inferred:
                blocks, power_refs = infer_layout_blocks(state)
                fixed_refs = sorted(set(fixed_refs or ()) | set(power_refs))
            result, resize = plan_sheet_layout(state, blocks, fixed_refs or (), canvas, intersects,
                                               orthogonal_path, check_layout)
            result.update(blocks=blocks, fixed_refs=fixed_refs or [], blocks_inferred=inferred)
            candidates = {candidate['name']: candidate for candidate in result['candidates']}
            branch_state = host.call('branchState') if supply_load_pins else None
            for name in result['ranked']:
                candidate = candidates[name]
                operations = placement_operations(state, candidate)
                if resize:
                    operations.insert(0, resize)
                if branch_state is not None:
                    projected = preview_route_state(branch_state, candidate['moves'],
                                                    candidate.get('junction_moves', {}), candidate['routes'])
                    supply = plan_parallel_trunk(projected, supply_load_pins, intersects, reuse_existing=True)
                    if 'reason' in supply:
                        candidate['supply'] = supply
                        continue
                    operations.extend(supply.pop('operations'))
                    candidate['supply'] = supply
                candidate['plan_id'] = register_layout_plan(
                    layout_plans, layout_identity(host, state), operations, terminal_groups(state['wires']))
                result['recommended'] = name
                break
            else:
                result['recommended'] = None
            return result

    @server.server.tool()
    @server.checked
    def native_plan_component_group(component_refs: list[str], axis: Literal['column', 'row'] = 'column',
                                    spacing: int | None = None) -> dict:
        """Preview aligned, evenly spaced parts in existing visual order. Apply selected component_moves with native_edit_batch, reroute affected wires, then call again for common collision-free reference/value label positions. Spacing is a grid multiple in sheet units, inferred from the group by default. Read-only."""
        from multisim_native_mcp.layout import intersects
        from multisim_unified_mcp.wiring import plan_component_group

        state = server.host.call("circuitState")
        return plan_component_group(state, set(component_refs), axis, intersects, spacing)

    @server.server.tool()
    @server.checked
    def native_compare_placements(candidates: dict[str, dict[str, tuple[int, int]]] | None = None,
                                  component_refs: list[str] | None = None) -> dict:
        """Compare named placement plans or generate layouts for a selected parallel fan-out block. Use component_refs alone for automatic plans connecting two horizontal stages with repeated vertical taps. Each lane reserves a separate track. With explicit candidates, keys are component references or junction IDs and offsets are 9-unit grid steps. Pass component_refs to include module-only metrics. Read-only. Apply reviewed moves and routes through native_edit_batch."""
        from multisim_native_mcp.layout import check_layout, intersects, orthogonal_path
        from multisim_unified_mcp.wiring import compare_placements, module_placement_proposals

        if not candidates and not component_refs:
            raise ValueError("Provide named placement candidates or select module components")
        state = server.host.call("circuitState")
        if candidates:
            proposals = [{"name": name, "moves": moves} for name, moves in candidates.items()]
            return compare_placements(state, proposals, intersects, orthogonal_path, component_refs)
        proposals, lanes = module_placement_proposals(state, set(component_refs))
        result = compare_placements(state, proposals, intersects, orthogonal_path, component_refs, check_layout)
        result["detected_lanes"] = lanes
        if not lanes:
            result["reason"] = "No repeated three-way branches connecting two horizontal stages and vertical taps"
        return result

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
    import sys

    # The gateway starts this script in the isolated Native Python environment.
    # Resolve the sibling package from the same checkout or installed release.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
