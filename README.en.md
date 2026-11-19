# multisim-mcp

[简体中文](README.md) · **English**

A local stdio MCP server for editing native NI Multisim schematics, verifying digital logic in F5 mode, and running COM analyses on saved circuits.

The project uses two upstream backends. [multisim-native-mcp](https://github.com/y-wi/multisim-native-mcp) provides native schematic and F5 operations, and [Multisim MCP](https://github.com/yxy050208/multisim-mcp) provides COM simulation.

## Quick start

[打开快速开始](QUICKSTART.md#english)

## Capabilities

| Workflow | Supported operations |
| --- | --- |
| Schematic editing | Create or open `.ms14`, place native parts from the master library, connect pins, label nets, and save. |
| Interactive F5 | Set logic inputs, read actual indicator states, and verify a truth table on the simulation clock. |
| COM simulation | Open the same circuit after handoff, run DC, AC, or transient analysis, and verify batches of saved logic cases. |

## Tested configuration

Windows with 32-bit Multisim `14.3.1500.0`, native backend `v0.5.2`, and COM backend `v1.3.1`. The full regression used 64-bit Python 3.13 for the gateway and 32-bit CPython 3.12 for both backends. F5 logic operations support the tested `INTERACTIVE_DIGITAL_CONSTANT` switches and `PROBE_DIG_RED` indicators.

## v0.5.0 preview

- Added `native_verify_f5_cases` for simulation-clock sampling, row-by-row verdicts, and switch-state restoration.
- Verified all eight live full-adder inputs, exact-file handoff, and all eight saved COM cases. Both verification modes detected an intentionally incorrect expectation.
- Includes a locked three-environment installer, standalone diagnostics, and backend fault isolation.

## License

The gateway is [MIT licensed](LICENSE). This is an independent interoperability project. Users supply a licensed Multisim installation and their own circuits. Native editing and live F5 interaction depend on version-specific Multisim internals. See the [third-party notices](THIRD_PARTY_NOTICES.md).
