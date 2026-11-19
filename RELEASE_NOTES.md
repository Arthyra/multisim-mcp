# v0.3.0 preview

This version exposes one stdio MCP server for real NI Multisim editing and COM analysis. It can search the main library, place native parts, run F5, operate stock digital switches, read red logic indicators, and hand a saved circuit to COM.

## Tested configuration

- Windows with 32-bit Multisim `14.3.1500.0`.
- Native backend `y-wi/multisim-native-mcp` `v0.5.2` and COM backend `yxy050208/multisim-mcp` `v1.3.1`.
- Live switch and indicator tools target `INTERACTIVE_DIGITAL_CONSTANT` and `PROBE_DIG_RED` on that build.

The local integration test covered a `/SimServer`-present startup, native master-library placement, all eight live full-adder input combinations, and exact-file handoff to COM. The saved-project truth-table check passed eight positive cases and rejected an intentionally incorrect expectation. See the [installation and configuration guide](README.md#local-setup).

## Example workflow

Use a working copy of a full-adder `.ms14` that has `A`, `B`, and `Cin` switches and `S` and `Cout` indicators. Replace the path below with its absolute location.

1. Call `native_open_circuit` with `{"source_path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`.
2. Search with `native_search_library` and `{"query":"74LS153N"}`. Place a spare instance with `native_place_component`, using the returned `TTL / 74LS` entry and a free position on the sheet. Remove the spare with `native_delete_object` before testing the existing full adder.
3. Start F5 with `native_control_interactive` and `{"action":"start"}`. With `A` and `B` low, call `native_set_logic_input` with `{"component_ref":"Cin","state":1}`. Read `native_read_logic_indicators` with `{"component_refs":["S","Cout"]}` after propagation. The expected states are `1` and `0`. Set `Cin` back to its starting level to restore the original input combination.
4. Call `handoff_to_com` with `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`. This stops F5, saves the active file, releases the editor session, and opens the saved file under COM. Check `ok`, `saved_path`, and `opened_path` in the receipt.
5. List the available signals with `com_enum_outputs`. Pass one returned voltage name as `output_name` to `com_run_transient`, then call `com_disconnect` when done.
