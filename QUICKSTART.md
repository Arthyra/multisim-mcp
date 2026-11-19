# 快速开始 · Quick start

[简体中文](#简体中文) · [English](#english)

## 简体中文

[返回中文 README](README.md)

### 安装要求

- Windows 上已安装并激活 NI Multisim 14.3。安装器针对实测的 32 位构建 `14.3.1500.0`。
- 网关使用 64 位 Python 3.11 或更新版本，两个后端使用 32 位 CPython 3.12。[Python Windows 下载页](https://www.python.org/downloads/windows/)
- 安装时需要联网下载固定版本的[原生后端 v0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2)、[COM 后端 v1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/)及其依赖。

### 安装并连接

克隆或下载仓库，在仓库目录打开 PowerShell，运行安装脚本。

```powershell
.\install.ps1
```

安装器默认使用 `python` 创建 64 位网关环境，并通过 `py -3.12-32` 查找 32 位解释器。也可以用 `-Python64` 和 `-Python32` 指定对应的 Python 可执行文件。如果 Multisim 安装在自定义位置，用 `-MultisimPath` 指定包含 `multisim.exe` 的目录。`-InstallDir` 可以指定其他安装目录。

脚本默认在 `.install` 下创建三个环境，并生成两份本地配置。每套环境按 [requirements](requirements/) 中的固定版本安装依赖，再运行 `pip check`。Codex 用户将生成的 `multisim.client.toml` 配置段加入客户端配置。其他 stdio MCP 客户端使用同一个可执行文件和参数，按客户端格式配置。已有的 `multisim.local.toml` 会被保留。

启动 MCP 后，可以调用 `native_runtime_status` 和 `com_runtime_status` 检查本地运行环境。需要将 `.ms14` 解码为 XML 时，参照 COM 后端的[可选编解码器安装说明](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install)。

### 安装诊断

```powershell
.\.install\gateway\Scripts\multisim-unified-mcp.exe --config .install\multisim.local.toml --doctor
```

`--doctor` 检查 Python 位数、所需模块、后端启动、Multisim 构建版本，以及 COM 工作进程的兼容性和注册状态。命令输出不含本机路径的 JSON 报告，并以只读方式检查运行环境。软件激活在 Multisim 中完成。

连接 MCP 后，用 `gateway_status` 查看两个后端的状态。某个后端启动失败或连接中断时，另一个后端仍可提供服务。修复故障后端的安装或配置后，重启网关即可重新加载。

### 第一个电路

1. 复制一份自己的 `.ms14` 工程。调用 `native_open_circuit`，传入 `{"source_path":"ABSOLUTE_PATH_TO_YOUR_CIRCUIT.ms14"}`，将占位符替换为工程的绝对路径。需要空白原理图时，用 `native_create_circuit`。
2. 调用 `native_search_library` 并传入 `{"query":"74LS153N"}` 搜索元件。这个型号可选择 `TTL / 74LS` 条目。将返回的型号、组和系列传给 `native_place_component`，在图纸空白处放置，并根据返回的引脚名接线。仿真原电路前，移除用于试放且尚未接线的元件。
3. 工程带有受支持的开关和指示灯时，可以进行 [F5 真值表验证](#验证-f5-数字逻辑)。需要对已保存电路做数值仿真时，继续第 4 步。
4. 将当前 `.ms14` 的绝对路径传给 `handoff_to_com`。交接成功后，先调用 `com_enum_outputs`，再将返回的电压信号作为 `output_name` 传给 `com_run_transient`。恢复原理图编辑前，先断开 COM 会话。

网关保留上游工具的参数和结果格式，并按顺序执行工具调用。启动 F5 的请求返回后，仿真仍可继续运行。两个后端各自管理自己的 Multisim 会话。

### 验证 F5 数字逻辑

调用一次 `native_verify_f5_cases`，为每行提供输入值和预期灯态。

```json
{
  "settle_time_s": 0.001,
  "cases": [
    {"name": "000", "inputs": {"A": 0, "B": 0, "Cin": 0}, "expected": {"S": 0, "Cout": 0}},
    {"name": "001", "inputs": {"A": 0, "B": 0, "Cin": 1}, "expected": {"S": 1, "Cout": 0}}
  ]
}
```

每行使用同一组输入和指示灯名称。工具按 F5 仿真时钟等待指定间隔，读取指示灯的实际状态，再与预期值比较。每行返回等待起点和采样时间。`settle_timeout_s` 设定每行实际等待的时限，默认 5 秒。如果仿真时钟未在时限内达到采样目标，当前行报告超时，后续行跳过。

验证结束后，工具恢复原开关状态。由工具启动的仿真会停止，调用前已经运行的仿真会继续。仿真处于暂停状态时，请先恢复运行或停止。

单独操作时，用 `native_control_interactive` 启动 F5，再给 `native_set_logic_input` 传入 `{"component_ref":"Cin","state":1}`。等信号传播后，给 `native_read_logic_indicators` 传入 `{"component_refs":["S","Cout"]}` 读取指示灯。用 `native_get_circuit_state` 确认元件名称。重复设置相同电平时，开关保持该电平。指示灯工具返回 `state` 和 `lit`，用于判断亮灯的图形属性不一致时返回 `null`。

F5 逻辑输入和读取功能在 Multisim `14.3.1500.0` 上支持原生 `INTERACTIVE_DIGITAL_CONSTANT` 开关和 `PROBE_DIG_RED` 指示灯。工具调用元件内部的交互事件，并从 Multisim 读取运行时图形状态，整个过程无需操作桌面鼠标或键盘。

<details>
<summary>手动安装、工程交接与回归测试</summary>

#### 手动安装

`install.ps1` 用于简化环境准备。安装完成后，客户端直接启动 `multisim-unified-mcp`。

手动安装时，先创建三个 Python 环境，再分别安装 [requirements](requirements/) 中对应的锁定依赖。在网关环境中运行 `python -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt` 和 `python -m pip install --no-deps .`。两个 32 位环境分别安装 `native.txt` 和 `com.txt` 中的依赖。每套环境装好后运行 `python -m pip check`。

将 [multisim.example.toml](multisim.example.toml) 复制为 `multisim.local.toml`，填入各后端 Python 的绝对路径。Multisim 使用自定义安装目录时，在 `[native.env]` 中设置 `MULTISIM_NATIVE_INSTALL`。配置客户端启动网关，并用 `--config` 传入该 TOML 文件的绝对路径。

#### 将工程交给 COM

调用 `handoff_to_com` 并传入 `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`，路径应指向当前打开的工程。网关先核对工程路径，再停止 F5、保存、释放原生会话，随后连接 COM 并打开保存后的文件。交接期间，网关按顺序执行这些操作。

检查返回结果中的 `ok` 和 `stage`。成功时，`stage` 为 `"complete"`，并返回 `saved_path` 和 `opened_path`。任一后端不可达时，交接会在 `check_backends` 阶段结束，原生工程保持当前状态。后续步骤失败时，结果会说明失败阶段以及原生会话是否已释放。如果保存成功而 COM 打开失败，可以使用返回的 `saved_path` 重试。

#### 批量验证已保存的逻辑用例

连接 COM 后，调用 `com_verify_logic_cases`。每份 `.ms14` 应预先设好该用例的输入状态。使用 `com_enum_outputs` 查询电压输出名称。

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

每个输出返回最后一个采样点的电压、时间、预期值、实测值和判定。默认 TTL 阈值下，电压不高于 0.8 V 判为低电平，不低于 2.0 V 判为高电平，中间区域判为不确定。按电路需要调整 `low_max_v`、`high_min_v` 和 `duration_s`。验证完成后，COM 会话保留最后打开的用例。

#### 连接现有原理图窗口

原生后端在查找编辑器时会跳过带 `/SimServer` 参数的无界面进程。仿真器和 COM 仍可使用这些进程。如果打开了多个原理图编辑器，用 `native_attach` 指定进程 ID 和工程索引。连接后可用 `native_list_projects` 查看工程及其索引，索引 0 对应该进程的第一份工程。

#### 回归测试

[GitHub Actions](.github/workflows/ci.yml) 会在推送到 `main` 或提交拉取请求时运行后端隔离测试，模拟后端启动失败和连接中断。也可以在网关 Python 环境中用 `python -m unittest discover -s tests -p test_backend_isolation.py` 本地运行。

运行完整集成测试前，按前文安装并配置后端，关闭 Multisim 原理图编辑窗口，以便测试独立启动。脚本使用你提供的工程的临时副本。

- `--adder` 指向一份全加器 `.ms14`。工程包含 `A`、`B`、`Cin` 原生逻辑开关、`S` 和 `Cout` 红色逻辑指示灯，以及 `V(OutProbe)` 和 `V(OutProbe1)` 两个电压输出。请保留未使用的 `U3` 位号，供测试临时放件。
- `--cases-dir` 指向八个已保存的原生用例，文件名为 `E5_000.ms14` 至 `E5_111.ms14`。每份用例使用相同的两个电压输出名，并设置与文件名对应的初始输入状态。

```powershell
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

将 `$adderFile` 和 `$caseDirectory` 设为本机测试数据。测试覆盖 `/SimServer` 运行时打开工程、主库放件、F5 八组真值表、交接路径核对和 COM 实测，其中包括故意填错预期值的用例。运行此测试需要本机已授权的 Multisim 和自备测试工程。

</details>

## English

[Back to English README](README.en.md)

### Requirements

- Windows with an activated NI Multisim 14.3 installation. The installer targets the tested 32-bit build `14.3.1500.0`.
- 64-bit Python 3.11 or newer for the gateway and 32-bit CPython 3.12 for the backends. The [Python Windows downloads](https://www.python.org/downloads/windows/) list 32-bit installers where available.
- Internet access to download the pinned [native backend v0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2), [COM backend v1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/), and their dependencies.

### Install and connect

Clone or download the repository. Open PowerShell in the checkout and run the installer.

```powershell
.\install.ps1
```

The installer uses `python` for the 64-bit environment and `py -3.12-32` to find the 32-bit interpreter. If needed, pass the Python executables explicitly with `-Python64` and `-Python32`. Pass the directory containing `multisim.exe` with `-MultisimPath` for a custom installation. `-InstallDir` selects a different output directory, preferably outside the checkout.

By default the script creates three environments under `.install` and writes two local configuration files. Each environment installs the exact runtime versions listed in [requirements](requirements/) and runs `pip check`. Paste the generated `multisim.client.toml` stanza into a Codex MCP configuration. For another stdio MCP client, use the same executable and arguments in that client's format. The script leaves client settings to the user and preserves an existing `multisim.local.toml`.

After starting the MCP server, call `native_runtime_status` and `com_runtime_status` to check the local runtime. XML decoding is optional and uses the COM backend's [codec setup](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install).

### Installation diagnosis

```powershell
.\.install\gateway\Scripts\multisim-unified-mcp.exe --config .install\multisim.local.toml --doctor
```

The command checks Python architecture, required modules, backend startup, the native Multisim build profile, and COM worker compatibility and registration. It prints a JSON report using fixed diagnostic codes and omits local paths. It uses read-only runtime checks and leaves license activation to the Multisim application.

Use `gateway_status` after connecting to the MCP. If a backend cannot start or its connection closes, the other backend keeps serving its tools. The status reports which backend needs attention. Fix its installation or configuration and restart the gateway to load it again.

### First circuit

1. Make a working copy of one of your `.ms14` circuits. Call `native_open_circuit` with `{"source_path":"ABSOLUTE_PATH_TO_YOUR_CIRCUIT.ms14"}`, substituting the file's absolute path. Use `native_create_circuit` to start from a blank sheet.
2. Search for a part with `native_search_library` and `{"query":"74LS153N"}`. For this part, `TTL / 74LS` is one exact match. Pass the returned name, group and family to `native_place_component`, choose a free position on the sheet, and inspect the returned pins before wiring. Remove an unwired trial part before running the existing circuit.
3. Use [live F5 logic verification](#verify-live-f5-logic) for a circuit with supported switches and indicators. For a saved-circuit analysis, proceed to step 4.
4. Call `handoff_to_com` with the active document's absolute `.ms14` path. After the handoff succeeds, call `com_enum_outputs`, then pass one returned voltage signal as `output_name` to `com_run_transient`. Disconnect COM before resuming native edits.

The gateway exposes a focused set of tools. It preserves upstream tool schemas and results and serializes tool calls. An interactive simulation can continue after its start request returns. Each backend manages its own Multisim session.

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

<details>
<summary>Manual setup, circuit handoff and regression tests</summary>

#### Manual installation

`install.ps1` is a convenience wrapper for setup. Clients launch the installed `multisim-unified-mcp` executable directly.

To install manually, create the three Python environments and install each matching lock file from [requirements](requirements/) using that environment's Python. For example, run `python -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt` in the gateway environment, then run `python -m pip install --no-deps .` there. Use `native.txt` and `com.txt` in the two 32-bit environments and run `python -m pip check` in each environment.

Copy [multisim.example.toml](multisim.example.toml) to `multisim.local.toml` and fill in the absolute backend Python paths. For a custom Multisim installation, set `MULTISIM_NATIVE_INSTALL` in `[native.env]`. Configure your stdio MCP client to launch the gateway executable with `--config` and the absolute path to that TOML file.

#### Hand a native document to COM

Call `handoff_to_com` with `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`, substituting the active document's absolute path. The gateway checks that the selected native source is this exact file before stopping F5. It then saves and releases the native session, connects COM, and opens the saved file. The full sequence runs under the gateway's tool-call lock.

Check `ok` and `stage` in the response. A successful result has `stage: "complete"` and includes `saved_path` and `opened_path`. If either backend cannot be reached, the handoff stops at `check_backends` before touching the native document. On later failures, the receipt reports the step and whether the native session was released. If the save succeeded and the COM open failed, use the returned `saved_path` to retry the COM open.

#### Verify saved logic cases

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

#### Attach to the schematic editor

The native worker excludes headless Multisim processes launched with `/SimServer`. They remain available to the simulator and COM backend. If several schematic editors are open, use `native_attach` with the desired editor's PID and document index. Use `native_list_projects` after attaching to inspect the indexed documents. Index 0 is the first document in that process.

#### Regression test

[GitHub Actions](.github/workflows/ci.yml) runs the backend isolation tests on pushes to `main` and pull requests. The tests simulate backend startup failures and a lost connection. Run them locally with `python -m unittest discover -s tests -p test_backend_isolation.py` from the gateway environment.

Install the gateway and configure both backends as above. Close Multisim schematic editor windows so the test can exercise isolated startup. The script uses temporary copies of the projects you supply.

- `--adder` is a full-adder `.ms14` with native `A`, `B`, and `Cin` interactive switches, `S` and `Cout` red logic indicators, and two voltage outputs named `V(OutProbe)` and `V(OutProbe1)`. The reference `U3` must be free for a temporary placement.
- `--cases-dir` is a directory of eight saved native `.ms14` input cases, named `E5_000.ms14` through `E5_111.ms14`. Each uses the same two voltage output names and starts with the named input bits.

```powershell
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

Set `$adderFile` and `$caseDirectory` to your local test inputs. The test exercises a native open with `/SimServer` running, master-library placement, live F5 for all eight truth-table rows, handoff path validation, and COM measurements including a deliberately incorrect expectation. It needs a licensed local Multisim installation and your own fixture files.

</details>
