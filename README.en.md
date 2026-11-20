# multisim-mcp

[简体中文](README.md) · **English**

Give an AI assistant a local MCP connection to NI Multisim so it can edit schematics, control interactive simulation, and check circuits against measured results.

[Open the quick-start guide](QUICKSTART.md#english)

## What you can do

- **Draw and inspect schematics.** Find specific parts in the Multisim master database, place and wire them, and save an editable `.ms14`. Use the native netlist to check electrical connections, inspect layout collisions separately, and export a schematic image when needed.
- **Check a digital truth table.** Run interactive simulation in Multisim (press F5 to start it manually), set each switch combination, and compare live indicators with expected values after a specified simulated interval. The tool then restores the original switches.
- **Measure voltages and read waveforms.** Use Native first for DC operating-point, transient, and AC analysis. Hand the saved circuit to COM for a COM-specific capability, a cross-check, or an explicit request. The gateway verifies that both backends use the same file.

## Compatibility and limits

- The real Multisim test environment is 32-bit NI Multisim `14.3.1500.0` on Windows, with Native `0.5.2` and COM `1.3.1`. The standard install uses one 64-bit gateway and two 32-bit CPython 3.12 backend environments. Native checks the build and key modules. Other Multisim builds are unverified.
- Automatic switch and indicator operations support `INTERACTIVE_DIGITAL_CONSTANT` and `PROBE_DIG_RED`. Truth-table rows are sampled after a fixed simulated interval. Verify timing behavior separately.
- COM numerical analysis uses the Windows automation interface. Logic checks on saved COM cases are intended for combinational circuits with held inputs. Native and COM run in separate processes behind one stdio MCP connection.

## Documentation and upstream projects

This project is in preview. The [quick-start guide](QUICKSTART.md#english) covers installation, diagnostics, and verification. The [agent instructions](AGENTS.md) cover circuit checks and protecting the original design.

[Multisim Native MCP](https://github.com/y-wi/multisim-native-mcp) provides native editing, checks, and simulation. [Multisim MCP](https://github.com/yxy050208/multisim-mcp) provides COM numerical analysis. This project adds row-by-row interactive truth-table checks and a same-file handoff between backends.

## License

The gateway uses the [MIT license](LICENSE). Users install and activate their own copy of Multisim. See the [third-party notices](THIRD_PARTY_NOTICES.md) for Frida, non-public interfaces, and upstream dependencies.
