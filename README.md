# multisim-mcp

One local MCP endpoint for editing real NI Multisim schematics, controlling F5 simulation, and running COM analysis. This is an independent interoperability project. See the [v0.3.0 release notes](RELEASE_NOTES.md) for a complete example.

## Requirements

- Windows with an activated NI Multisim 14.3 installation. The installer targets the tested 32-bit build `14.3.1500.0`.
- 64-bit Python 3.11 or newer for the gateway and 32-bit CPython 3.12 for the backends. The [Python Windows downloads](https://www.python.org/downloads/windows/) list 32-bit installers where available.
- Internet access to download the pinned [native backend v0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2), [COM backend v1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/), and their dependencies.

## Local setup

Run the installer from this checkout in PowerShell.

```powershell
.\install.ps1
```

The installer uses `python` for the 64-bit environment and `py -3.12-32` to find the 32-bit interpreter. If needed, pass the Python executables explicitly with `-Python64` and `-Python32`. Pass the directory containing `multisim.exe` with `-MultisimPath` for a custom installation. `-InstallDir` selects a different output directory, preferably outside the checkout.

By default the script creates three environments under `.install` and writes two local configuration files. Paste the generated `multisim.client.toml` stanza into a Codex MCP configuration. For another stdio MCP client, use the same executable and arguments in that client's format. The script leaves client settings to the user and preserves an existing `multisim.local.toml`.

After starting the MCP server, call `native_runtime_status` and `com_runtime_status` to check the local runtime. For manually provisioned or existing environments, start with [multisim.example.toml](multisim.example.toml) and supply absolute Python paths. XML decoding is optional and uses the COM backend's [codec setup](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install).

## Workflow

1. Create or open a real `.ms14` with `native_create_circuit` or `native_open_circuit`. If several Multisim processes are open, use `native_attach` with the process ID and project index.
2. Use `native_search_library`, then pass the exact name, group and family to `native_place_component`. Inspect the returned pins before wiring.
3. For F5, call `native_control_interactive` and check `transition_confirmed`. Use `native_set_logic_input` and `native_read_logic_indicators` for live logic tests.
4. Call `handoff_to_com` with the active document's absolute `.ms14` path. After a successful handoff, call `com_enum_outputs` before requesting a measurement with `com_run_transient`. Disconnect COM before resuming native edits.

The gateway exposes a focused set of tools. It preserves upstream tool schemas and results and serializes tool calls. An interactive simulation can continue after its start request returns. Each backend manages its own Multisim session.

### Hand a native document to COM

Call `handoff_to_com` with `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`, substituting the active document's absolute path. The gateway checks that the selected native source is this exact file before stopping F5. It then saves and releases the native session, connects COM, and opens the saved file. The full sequence runs under the gateway's tool-call lock.

Check `ok` and `stage` in the response. A successful result has `stage: "complete"` and includes `saved_path` and `opened_path`. On failure, the response identifies the step and reports whether the native session was released. If the save succeeded and the COM open failed, use the returned `saved_path` to retry the COM open.

### Verify live F5 logic

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

## Regression test

Install the gateway and configure both backends as above. Close Multisim schematic editor windows so the test can exercise isolated startup. The script uses temporary copies of the projects you supply.

- `--adder` is a full-adder `.ms14` with native `A`, `B`, and `Cin` interactive switches, `S` and `Cout` red logic indicators, and two voltage outputs named `V(OutProbe)` and `V(OutProbe1)`. The reference `U3` must be free for a temporary placement.
- `--cases-dir` is a directory of eight saved native `.ms14` input cases, named `E5_000.ms14` through `E5_111.ms14`. Each uses the same two voltage output names and starts with the named input bits.

```powershell
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

Set `$adderFile` and `$caseDirectory` to your local test inputs. The test exercises a native open with `/SimServer` running, master-library placement, live F5 for all eight truth-table rows, handoff path validation, and COM measurements including a deliberately incorrect expectation. It needs a licensed local Multisim installation and your own fixture files.

## Third-party software

This repository contains the gateway code and configuration examples. Install Multisim and the two backends separately. See [third-party notices](THIRD_PARTY_NOTICES.md).
