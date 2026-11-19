# multisim-mcp

[简体中文](README.md) · **English**

A local MCP server for creating and editing native NI Multisim schematics, testing live digital logic, and running COM analyses on saved circuits. It presents one stdio endpoint to an MCP client and uses a locally installed copy of Multisim. This is an independent interoperability project.

The current preview is [v0.5.0](#v050-preview). Start with [quick start](#quick-start) for your first circuit.

## What you can do

| Workflow | Supported operations |
| --- | --- |
| Schematic editing | Open or create `.ms14`, search the Multisim master library, place native parts, connect pins, label nets, and save. |
| Interactive F5 | Start and stop simulation, set supported switches, read real indicator states, and check a complete truth table on the simulation clock. |
| Saved-circuit simulation | Hand off the exact native document to COM, run DC, AC or transient analysis, and check batches of saved logic cases. |

## Requirements

- Windows with an activated NI Multisim 14.3 installation. The installer targets the tested 32-bit build `14.3.1500.0`.
- 64-bit Python 3.11 or newer for the gateway and 32-bit CPython 3.12 for the backends. The [Python Windows downloads](https://www.python.org/downloads/windows/) list 32-bit installers where available.
- Internet access to download the pinned [native backend v0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2), [COM backend v1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/), and their dependencies.

## Quick start

Clone or download the repository. Open PowerShell in the checkout and run the installer.

```powershell
.\install.ps1
```

The installer uses `python` for the 64-bit environment and `py -3.12-32` to find the 32-bit interpreter. If needed, pass the Python executables explicitly with `-Python64` and `-Python32`. Pass the directory containing `multisim.exe` with `-MultisimPath` for a custom installation. `-InstallDir` selects a different output directory, preferably outside the checkout.

By default the script creates three environments under `.install` and writes two local configuration files. Each environment installs the exact runtime versions listed in [requirements](requirements/) and runs `pip check`. Paste the generated `multisim.client.toml` stanza into a Codex MCP configuration. For another stdio MCP client, use the same executable and arguments in that client's format. The script leaves client settings to the user and preserves an existing `multisim.local.toml`.

After starting the MCP server, call `native_runtime_status` and `com_runtime_status` to check the local runtime. XML decoding is optional and uses the COM backend's [codec setup](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install).

### Manual installation

`install.ps1` is a convenience wrapper for setup. Clients launch the installed `multisim-unified-mcp` executable directly.

To install manually, create the three Python environments and install each matching lock file from [requirements](requirements/) using that environment's Python. For example, run `python -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt` in the gateway environment, then run `python -m pip install --no-deps .` there. Use `native.txt` and `com.txt` in the two 32-bit environments and run `python -m pip check` in each environment.

Copy [multisim.example.toml](multisim.example.toml) to `multisim.local.toml` and fill in the absolute backend Python paths. For a custom Multisim installation, set `MULTISIM_NATIVE_INSTALL` in `[native.env]`. Configure your stdio MCP client to launch the gateway executable with `--config` and the absolute path to that TOML file.

### Installation diagnosis

```powershell
.\.install\gateway\Scripts\multisim-unified-mcp.exe --config .install\multisim.local.toml --doctor
```

The command checks Python architecture, required modules, backend startup, the native Multisim build profile, and COM worker compatibility and registration. It prints a JSON report using fixed diagnostic codes and omits local paths. It uses read-only runtime checks and leaves license activation to the Multisim application.

Use `gateway_status` after connecting to the MCP. If a backend cannot start or its connection closes, the other backend keeps serving its tools. The status reports which backend needs attention. Fix its installation or configuration and restart the gateway to load it again.

## First circuit

1. Make a working copy of one of your `.ms14` circuits. Call `native_open_circuit` with `{"source_path":"ABSOLUTE_PATH_TO_YOUR_CIRCUIT.ms14"}`, substituting the file's absolute path. Use `native_create_circuit` to start from a blank sheet.
2. Search for a part with `native_search_library` and `{"query":"74LS153N"}`. For this part, `TTL / 74LS` is one exact match. Pass the returned name, group and family to `native_place_component`, choose a free position on the sheet, and inspect the returned pins before wiring. Remove an unwired trial part before running the existing circuit.
3. Use [live F5 logic verification](#verify-live-f5-logic) for a circuit with supported switches and indicators. For a saved-circuit analysis, proceed to step 4.
4. Call `handoff_to_com` with the active document's absolute `.ms14` path. After the handoff succeeds, call `com_enum_outputs`, then pass one returned voltage signal as `output_name` to `com_run_transient`. Disconnect COM before resuming native edits.

The gateway exposes a focused set of tools. It preserves upstream tool schemas and results and serializes tool calls. An interactive simulation can continue after its start request returns. Each backend manages its own Multisim session.

### Hand a native document to COM

Call `handoff_to_com` with `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`, substituting the active document's absolute path. The gateway checks that the selected native source is this exact file before stopping F5. It then saves and releases the native session, connects COM, and opens the saved file. The full sequence runs under the gateway's tool-call lock.

Check `ok` and `stage` in the response. A successful result has `stage: "complete"` and includes `saved_path` and `opened_path`. If either backend cannot be reached, the handoff stops at `check_backends` before touching the native document. On later failures, the receipt reports the step and whether the native session was released. If the save succeeded and the COM open failed, use the returned `saved_path` to retry the COM open.

### Verify live F5 logic

Call `native_verify_f5_cases` once with the input and expected indicator states for each row.

```json
{
  "settle_time_s": 0.001,
  "cases": [
    {"name": "000", "inputs": {"A": 0, "B": 0, "Cin": 0}, "expected": {"S": 0, "Cout": 0}},
    {"name": "001", "inputs": {"A": 0, "B": 0, "Cin": 1}, "expected": {"S": 1, "Cout": 0}}
  ]
}
```

Use the same input and indicator references in every row. The tool waits for the configured interval on the native F5 simulation clock, then records the measured indicator states and compares them with the expected bits. Each result includes the settling baseline and sample time. The wall-clock limit per row is `settle_timeout_s` (default 5 seconds). If the clock fails to advance far enough, the row reports a timeout and the remaining rows are skipped.

The tool restores the switch states it recorded at the start. It stops F5 if it started the run, and leaves an already-running simulation running. A paused simulation needs to be resumed or stopped before calling the tool.

For individual interactions, start F5 and use the separate input and indicator tools.

Start F5 with `native_control_interactive`, set the input using `native_set_logic_input` with `{"component_ref":"Cin","state":1}`, and read the outputs after propagation using `native_read_logic_indicators` with `{"component_refs":["S","Cout"]}`. Use the exact reference names from `native_get_circuit_state`. Repeating a requested input level leaves the switch at that level. The indicator tool returns `state` and `lit` from the Multisim runtime, with `null` if the graphic properties disagree.

Live logic I/O supports stock `INTERACTIVE_DIGITAL_CONSTANT` switches and `PROBE_DIG_RED` indicators on the tested Multisim 14.3.1500.0 runtime. The worker calls the component's internal event handler and reads its runtime graphics through the native backend. These calls do not move the desktop pointer or send physical key presses.

### Verify saved logic cases

With COM connected, call `com_verify_logic_cases` to verify a table of saved `.ms14` circuits in one request. Prepare each case's input state in its circuit file before running the tool. Find the voltage output names with `com_enum_outputs`.

```json
{
  "cases": [
    {"name": "000", "path": "ABSOLUTE_PATH_TO_ADDER_000.ms14",
     "expected": {"V(OutProbe)": 0, "V(OutProbe1)": 0}},
    {"name": "111", "path": "ABSOLUTE_PATH_TO_ADDER_111.ms14",
     "expected": {"V(OutProbe)": 1, "V(OutProbe1)": 1}}
  ]
}
```

Each output includes the last sampled voltage, timestamp, expected and observed bits, and a verdict. At the default TTL thresholds, at most 0.8 V is low and at least 2.0 V is high. An in-between voltage is indeterminate and fails the check. Set `low_max_v`, `high_min_v` and `duration_s` for a different circuit. The tool leaves the COM session on the last case it opened.

### Attach to the schematic editor

The native worker excludes headless Multisim processes launched with `/SimServer`. They remain available to the simulator and COM backend. If several schematic editors are open, use `native_attach` with the desired editor's PID and document index. Use `native_list_projects` after attaching to inspect the indexed documents. Index 0 is the first document in that process.

## Tested configurations

- Windows with 32-bit Multisim `14.3.1500.0`, the native backend `v0.5.2`, and the COM backend `v1.3.1`.
- Gateway on 64-bit Python 3.13 and both backends on 32-bit CPython 3.12 in the full integration run.
- F5 logic operations on stock `INTERACTIVE_DIGITAL_CONSTANT` switches and `PROBE_DIG_RED` indicators.

Native editing and live interaction use version-specific implementation details of Multisim. See the [third-party notices](THIRD_PARTY_NOTICES.md) for licensing and interoperability information.

## Regression test

Run the backend isolation tests with `python -m unittest discover -s tests -p test_backend_isolation.py` from the installed gateway environment. They use local test processes to simulate startup failures and a lost connection.

Install the gateway and configure both backends as above. Close Multisim schematic editor windows so the test can exercise isolated startup. The script uses temporary copies of the projects you supply.

- `--adder` is a full-adder `.ms14` with native `A`, `B`, and `Cin` interactive switches, `S` and `Cout` red logic indicators, and two voltage outputs named `V(OutProbe)` and `V(OutProbe1)`. The reference `U3` must be free for a temporary placement.
- `--cases-dir` is a directory of eight saved native `.ms14` input cases, named `E5_000.ms14` through `E5_111.ms14`. Each uses the same two voltage output names and starts with the named input bits.

```powershell
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

Set `$adderFile` and `$caseDirectory` to your local test inputs. The test exercises a native open with `/SimServer` running, master-library placement, live F5 for all eight truth-table rows, handoff path validation, and COM measurements including a deliberately incorrect expectation. It needs a licensed local Multisim installation and your own fixture files.

## v0.5.0 preview

- Added `native_verify_f5_cases` to set inputs, sample actual indicator states after the requested simulation time, report row-by-row verdicts, and restore the original switch settings.
- Tested all eight live full-adder rows, exact-file handoff, and all eight saved COM cases. An intentionally incorrect expectation was rejected in each verification mode.
- Includes a locked three-environment installer, standalone diagnostics, and separate availability for each backend.

## License

The gateway is [MIT licensed](LICENSE). The installer obtains the pinned upstream packages from their release sources. Users supply their own licensed Multisim installation and test circuits. See the [third-party notices](THIRD_PARTY_NOTICES.md).
