# multisim-mcp

[简体中文](README.md) · **English**

An unofficial local MCP integration for NI Multisim. It lets an AI assistant edit schematics, control interactive simulation, and check circuits against measured results.

[Open the quick-start guide](QUICKSTART.md#english)

## What you can do

- **Draw and inspect schematics.** Find specific parts in the Multisim master database, place and wire them, and save an editable `.ms14`. Use the native netlist to check electrical connections, inspect layout collisions separately, and export a schematic image when needed.
- **Lay out and route an entire schematic.** Infer component groups from native connectivity, or define groups and lock parts in place. Compare placements, shared routing corridors, and supply rails. Review the plan before applying it, then verify terminal connectivity and check for new layout issues. Export before-and-after images at the same scale. See [schematic layout and routing](QUICKSTART.md#schematic-layout-and-routing).
- **Check a digital truth table.** Run interactive simulation in Multisim (press F5 to start it manually), set each switch combination, and compare live indicators with expected values after a specified simulated interval. The tool then restores the original switches.
- **Measure voltages and read waveforms.** Use Native first for DC operating-point, transient, and AC analysis. Hand the saved circuit to COM for a COM-specific capability, a cross-check, or an explicit request. The gateway verifies that both backends use the same file.

## Compatibility and limits

- Real tests verified 32-bit NI Multisim `14.3.3.0` on 64-bit Windows, with `multisim.exe` file version `14.3.1500.0`. The installer checks the file version. Native verifies the fingerprints of key modules before use. See the [compatibility matrix](QUICKSTART.md#multisim-143-compatibility) for patch and edition coverage.
- Automatic switch and indicator operations support `INTERACTIVE_DIGITAL_CONSTANT` and `PROBE_DIG_RED`. Truth-table rows are sampled after a fixed simulated interval. Verify timing behavior separately.
- The routing planner compares a finite set of candidates. Review the result on a working copy, paying particular attention to feedback loops, power rails, and manually routed areas.
- COM numerical analysis uses the Windows automation interface. Logic checks on saved COM cases are intended for combinational circuits with held inputs. Native and COM run in separate processes behind one stdio MCP connection.

## Documentation and upstream projects

Run `./install.ps1` in PowerShell, then add the generated stdio configuration to your MCP client. [QUICKSTART.md](QUICKSTART.md#english) covers requirements, diagnostics, and verification. [AGENTS.md](AGENTS.md) covers circuit checks and protecting the original design.

[Multisim Native MCP v0.5.2](https://github.com/y-wi/multisim-native-mcp/tree/v0.5.2) provides native editing, checks, and simulation. [Multisim MCP v1.3.1](https://github.com/yxy050208/multisim-mcp/tree/v1.3.1) provides COM numerical analysis. This project adds whole-sheet layout and routing, row-by-row interactive truth-table checks, and a same-file handoff.

## License

The gateway uses the [MIT license](LICENSE). Users install and activate their own copy of Multisim. See the [third-party notices](THIRD_PARTY_NOTICES.md) for Frida, non-public interfaces, and upstream dependencies.
