# 快速开始 · Quick start

[简体中文](#简体中文) · [English](#english) · [共用命令与参数示例 / Shared examples](#shared-examples)

## 简体中文

### 安装要求

- 64 位 Windows 上已安装并激活 32 位 NI Multisim。实机支持产品版本 `14.3.3.0`（Patch 3），可执行文件版本 `14.3.1500.0`，并且需要通过模块指纹校验。其他构建见[兼容性矩阵](#multisim-143-compatibility)。
- 网关需要 64 位 Python 3.11 或以上，实测使用 3.13。两个后端需要 32 位 CPython 3.12。[Python 下载](https://www.python.org/downloads/windows/)
- 安装时联网下载 [Native 0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2)、[COM 1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/)和锁定的依赖。

### 安装并连接

在仓库目录打开 PowerShell，执行[安装命令](#install-and-diagnose)。安装器默认使用 `python`，也会尝试从 `py` 找到 64 位 Python 3.13。它用 `py -3.12-32` 查找 32 位解释器。可用 `-Python64` 和 `-Python32` 指定相应的 `python.exe`，用 `-MultisimPath` 指定含有 `multisim.exe` 的目录。

默认在 `.install` 下建立三个隔离环境。把生成的 `multisim.client.toml` 配置段复制到 MCP 客户端。其他 stdio 客户端也可使用其中的命令和参数。可用 `-InstallDir` 改安装位置。重跑会复用位数和版本符合要求的环境，更新锁定依赖与网关，保留已存在的 `multisim.local.toml`。

### 安装诊断

执行[诊断命令](#install-and-diagnose)，获得可分享的 JSON 报告。Native 的 `basic_ok` 表示基础运行环境通过检查，`f5_logic_compatible` 表示 F5 开关和指示器扩展通过检查，COM 的 `runtime.ok` 表示工作进程和注册状态通过检查。顶层 `ok` 表示完整安装通过，包括 COM 同步采样接口。失败项有相应的原因或检查字段。诊断无需打开工程，也不会实际运行仿真。

客户端里的 `gateway_status` 用 `available` 表示后端会话仍连接，用 `reachable` 表示在 0.5 秒内收到 MCP ping 回复。后端忙时可能仍连接但未及时应答。当前会话保留启动时发现的工具列表，断线后调用该后端的工具会返回 `backend_unavailable`。修复后重启网关即可重新发现工具。

### 第一个电路

1. 用 `native_create_circuit` 创建空白工程。编辑现有工程时，先用 `native_open_copy` 将源文件复制到指定的未占用绝对路径。要把这份副本交给 COM，请关闭后通过 `native_open_circuit` 重新打开副本。
2. 用 `native_search_library` 按型号搜索元件。以 `74LS153N` 为例，可选 `TTL / 74LS` 条目。将库条目的型号、组和系列传给 `native_place_component`，再按实际引脚名接线。
3. 电气连接用 `native_get_circuit_state` 的引脚和网络以及 `native_check_netlist` 的网表和告警来核对，图面碰撞用 `native_check_layout` 单独检查。如检查结果提示句柄失效，后续编辑使用它返回的新句柄。用 `native_export_image` 导出电路图。
4. 数字逻辑先做[交互仿真](#验证-f5-数字逻辑)。数值分析先停止交互仿真，再用 `native_run_analysis` 跑工作点或瞬态，或用 `native_run_ac_sweep` 做交流扫频。将返回的 `raw_path` 交给 `native_read_waveform` 读取采样点。交接 COM 见下文。

### 验证 F5 数字逻辑

用 `native_verify_f5_cases` 逐组核对实际灯态，见 [F5 参数示例](#f5-input)。所有行应使用同一组开关与指示灯名称。工具在每次拨动后等待指定的仿真时间，再读取一次灯态。这个时间条件不判断输出是否稳定。超时会停止后续用例，灯态不确定会单独标为 `indeterminate`。

仅支持 Multisim 自带的 `INTERACTIVE_DIGITAL_CONSTANT` 与 `PROBE_DIG_RED`。工具在结束时恢复开关，只停止它自己启动的仿真。原本暂停的仿真需先手动恢复或停止。单独操作时可用 `native_control_interactive`、`native_set_logic_input` 与 `native_read_logic_indicators`。这些工具直接调用 Multisim 的交互事件，无需操作桌面鼠标或键盘。

<details>
<summary>手动安装、工程交接与回归测试</summary>

#### 手动安装

为三个 Python 环境分别安装 [requirements](requirements/) 中对应的锁定文件，再安装网关包，见[手动安装命令](#manual-install)。复制 [multisim.example.toml](multisim.example.toml) 为 `multisim.local.toml`，填写解释器绝对路径。自定义 Multisim 目录填写 `[native.env]` 中的 `MULTISIM_NATIVE_INSTALL`，客户端使用 `multisim-unified-mcp --config` 指向这份配置。

#### 将工程交给 COM

需要 COM 专有能力、跨后端复核或明确指定 COM 时，把当前正在编辑、允许覆盖保存的 `.ms14` 绝对路径传给 `handoff_to_com`。网关核对文件，依次停止交互仿真、保存、释放 Native、连接 COM 并打开同一文件。成功结果包含 `saved_path` 和 `opened_path`。失败时查看 `stage`、各步骤的确认字段和 `recovery`。取消或超时后，当前步骤可能已经执行，请核实文件和当前会话再重试。继续原生编辑前断开 COM。

用 `com_enum_outputs` 查询电压信号，再按需用 `com_run_transient` 等工具。可选 XML 编解码见 [COM 上游安装说明](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install)。

#### 批量验证已保存的逻辑用例

用 `com_verify_logic_cases` 验证已经设好恒定输入的 `.ms14`，见 [COM 参数示例](#com-input)。一个用例的所有输出来自同一次瞬态仿真，使用同一采样时刻的末点。默认 TTL 阈值为 ≤0.8 V 低电平、≥2.0 V 高电平，中间电压不通过。按传播时间设置 `duration_s`。此工具用于组合逻辑，不能代替时序验证。自定义 COM 启动命令若缺少同步多通道接口，仍可验证单输出。

#### 连接现有原理图窗口

有多个编辑器时用 `native_attach` 指定进程 ID 和工程索引。用 `native_list_projects` 查看项目。编辑器发现会跳过 `/SimServer` 进程。

#### 回归测试

[CI](.github/workflows/ci.yml) 运行无需 Multisim 的单元和 MCP 协议测试。实机回归需要授权的 Multisim 和自己的工程夹具，脚本只操作临时副本。关闭其他 Multisim 原理图窗口后，参照[测试命令与夹具](#tests)运行。

</details>

## English

### Requirements

- 64-bit Windows with an activated 32-bit NI Multisim installation. The tested product version is `14.3.3.0` (Patch 3), with executable file version `14.3.1500.0` and matching module fingerprints. See the [compatibility matrix](#multisim-143-compatibility) for other builds.
- 64-bit Python 3.11 or later for the gateway (tested on 3.13) and 32-bit CPython 3.12 for both backends. [Python downloads](https://www.python.org/downloads/windows/)
- Internet access to install the pinned [Native 0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2), [COM 1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/), and runtime dependencies.

### Install and connect

Open PowerShell in the repository and run the [install command](#install-and-diagnose). The installer tries `python` and can fall back to the launcher for 64-bit Python 3.13. It uses `py -3.12-32` to find 32-bit Python. Supply explicit executables using `-Python64` and `-Python32`. Use `-MultisimPath` for a custom directory containing `multisim.exe`.

The installer creates three isolated environments under `.install` by default. Copy the generated `multisim.client.toml` fragment into your MCP client or use its command and arguments in another stdio client. Use `-InstallDir` to change the location. Rerunning reuses environments with a valid Python architecture and version, updates pinned dependencies and the gateway, and preserves existing `multisim.local.toml`.

### Installation diagnosis

Run the [diagnostic command](#install-and-diagnose) for a shareable JSON report. Native's `basic_ok` reports base runtime compatibility and `f5_logic_compatible` covers the F5 switch and indicator extension. COM's `runtime.ok` covers worker compatibility and registration. Top-level `ok` covers the full installation, including synchronized COM sampling. Check the corresponding reason or check field on failure. Diagnostics do not open a circuit or run a simulation.

In `gateway_status`, `available` means the backend session is still connected. `reachable` means it answered an MCP ping within 0.5 seconds. A busy backend can remain connected and miss that deadline. The session keeps the tool list discovered at startup. Calls to a disconnected backend return `backend_unavailable`. Restart the gateway after fixing the backend to discover its tools again.

### First circuit

1. Use `native_create_circuit` for a blank project. Before editing an existing project, use `native_open_copy` with an unused absolute output path. To hand that copy to COM, close it and reopen the copy with `native_open_circuit` first.
2. Find exact parts with `native_search_library`. For example, `74LS153N` has a `TTL / 74LS` entry. Pass the name, group, and family to `native_place_component`, then wire the actual pins.
3. Inspect pins and nets with `native_get_circuit_state`, review the native netlist and warnings with `native_check_netlist`, and inspect layout separately with `native_check_layout`. If a check invalidates handles, use the returned replacements for later edits. Use `native_export_image` to export the schematic.
4. Check digital logic using [interactive simulation](#verify-live-f5-logic). For numerical analysis, stop interactive simulation and use `native_run_analysis` for operating point or transient analysis, or `native_run_ac_sweep` for AC. Pass the returned `raw_path` to `native_read_waveform` to read samples. See the COM handoff instructions below when needed.

### Verify live F5 logic

Provide one input combination and expected indicator states per row to `native_verify_f5_cases`. See [F5 input](#f5-input). Use the same switch and indicator names throughout. The tool waits a specified simulated interval after switching inputs and takes one sample. That interval does not establish that the circuit is stable. A timeout stops the remaining cases. Uncertain indicator readings are marked `indeterminate`.

The supported parts are Multisim's built-in `INTERACTIVE_DIGITAL_CONSTANT` and `PROBE_DIG_RED`. The tool restores the switches and stops a run it started. Resume or stop an already-paused run first. For individual operations, use `native_control_interactive`, `native_set_logic_input`, and `native_read_logic_indicators`. These call Multisim's native events without moving the desktop pointer or typing keys.

<details>
<summary>Manual setup, circuit handoff, and regression tests</summary>

#### Manual installation

Install the matching [requirements](requirements/) lock in each Python environment, then install the gateway package. See the [manual commands](#manual-install). Copy [multisim.example.toml](multisim.example.toml) to `multisim.local.toml`, fill in absolute interpreter paths, and use `MULTISIM_NATIVE_INSTALL` under `[native.env]` for a custom Multisim directory. Point the client to the config with `multisim-unified-mcp --config`.

#### Hand a native document to COM

Use COM for a capability that needs it, a cross-check, or an explicit request. Pass the absolute path of the active `.ms14` that may be saved in place to `handoff_to_com`. The gateway checks the path, stops interactive simulation, saves, releases Native, connects COM, and opens the same file. On success it reports `saved_path` and `opened_path`. On failure, check `stage`, the confirmation fields, and `recovery`. A cancelled or timed-out step may have taken effect. Inspect the document and session before retrying. Disconnect COM before editing again.

Find voltage outputs with `com_enum_outputs` and use tools such as `com_run_transient` to measure them. See the [COM upstream install guidance](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install) for optional XML conversion.

#### Verify saved logic cases

Call `com_verify_logic_cases` with saved `.ms14` cases whose inputs are held constant. See [COM input](#com-input). Each case uses a single transient run and compares the final sample at the same time across channels. Default TTL thresholds are ≤0.8 V for low and ≥2.0 V for high. In-between values fail. Set `duration_s` to cover propagation. Use this for combinational logic and validate timing separately. A custom COM server without synchronized multi-channel sampling can still verify one output per case.

#### Attach to the schematic editor

If several editors are open, use `native_attach` with the desired PID and document index. Inspect the project list with `native_list_projects`. Editor discovery skips `/SimServer` instances.

#### Regression test

[CI](.github/workflows/ci.yml) runs unit and MCP protocol tests without Multisim. The real regression uses temporary copies of user-supplied fixtures and requires an activated local installation. Close other Multisim editor windows and follow the [test commands and fixture guide](#tests).

</details>

## Multisim 14.3 compatibility

产品版本、安装包版本和可执行文件版本分别记录。通过安装器的文件版本检查后，仍需用 `--doctor` 核对运行时。COM 取决于 32 位 Automation API 的注册和授权。 / Product, package, and executable versions are tracked separately. Run `--doctor` after the installer version check. COM requires the 32-bit Automation API to be registered and available under your license.

| 环境 / Environment | 证据 / Evidence | 状态 / Status |
| --- | --- | --- |
| Patch 3，32 位 / x86，Education 安装包 / installer | 本项目实机验证，产品 / product `14.3.3.0`，NI 包 / package `14.3.3.49192`，`multisim.exe` 和 / and `MSInterface.dll` 文件 / file version `14.3.1500.0`。 | **已验证并支持 / Verified and supported**，Native、F5、COM、工程交接 / handoff |
| Patch 3，32 位 / x86，Professional | 外部项目实测 COM，报告相同的产品及文件版本。 / An independent project tested COM and reported the same product and file versions. | **仅有外部 COM 证据 / External COM evidence only**，本项目未验收 / unverified here。[External test report](https://github.com/egg-pikaq/multisim-agent#当前本机验证状态) |
| `14.3.0` | NI 核心包 / core package `14.3.0.49153-0+f1`，`multisim.exe` 文件版本 / file version `14.3.1482.0`。 | 安装器拒绝，Native 和 F5 指纹不匹配，COM 待实机验证。 / Rejected by the installer, Native and F5 fingerprints differ, COM untested here. |
| `14.3.1` | NI 核心包 / core package `14.3.1.49156-0+f4`，`multisim.exe` 文件版本 / file version `14.3.1480.0`。 | 安装器拒绝，Native 和 F5 指纹不匹配，COM 待实机验证。 / Rejected by the installer, Native and F5 fingerprints differ, COM untested here. |
| `14.3.2` | NI 核心包 / core package `14.3.2.49162-0+f10`，`multisim.exe` 文件版本 / file version `14.3.1482.0`。 | 安装器拒绝，Native 和 F5 指纹不匹配，COM 待实机验证。 / Rejected by the installer, Native and F5 fingerprints differ, COM untested here. |
| Native 模块或 F5 指纹不匹配 / Native or F5 fingerprint mismatch | Native 校验失败时拒绝启动或附加编辑器。基础指纹通过且仅 F5 指纹不匹配时，仍可使用基础 Native 功能。 / A base mismatch blocks Native launch and attachment. An F5-only mismatch blocks switch and indicator automation. | **对应功能不兼容 / Affected capability unsupported** |
| 64 位 Multisim 进程或只有 64 位 COM 注册 / 64-bit process or 64-bit-only COM registration | 当前 Native 配置针对 x86，COM 工作进程也是 32 位。 / The Native profile and COM worker target x86. | **不支持 / Unsupported** |

NI 的 Education 和 Professional 索引为四个版本分别列出相同的核心包 SHA-256。按 NI 索引验证并离线解包后，前三版与 Patch 3 的全部 15 项 Native 指纹及独立的 `interact.dll` 指纹均不匹配。四版 `MSInterface.dll` 中用于 COM 注册的资源逐字节相同，类型库的差异集中在 MIDL 编译信息。这为 COM 单独验证提供了依据。 / The NI Education and Professional feeds list the same core package checksum for each of the four versions. Offline extraction of checksum-verified packages showed that all 15 Native fingerprints and the separate `interact.dll` fingerprint differ from Patch 3 in the three earlier versions. The COM registration resource is identical across all four versions, and type library differences are confined to MIDL build metadata. This supports testing COM separately.

NI 资料 / NI sources · [Education package index](https://download.ni.com/support/nipkg/products/ni-c/ni-cds-educational/14.3/released/Packages) · [Professional package index](https://download.ni.com/support/nipkg/products/ni-c/ni-cds-professional/14.3/released/Packages) · [14.3 Readme](https://www.ni.com/pdf/manuals/circuit-design-suite-14.3.html) · [Patch 1](https://www.ni.com/en/support/documentation/supplemental/25/circuit-design-suite-14-3-patch-1-details.html) · [Patch 2](https://www.ni.com/en/support/documentation/supplemental/25/circuit-design-suite-14-3-patch-2-details.html) · [Patch 3](https://www.ni.com/en/support/documentation/supplemental/26/circuit-design-suite-14-3-patch-3-details.html) · [Automation API](https://www.ni.com/docs/en-US/bundle/multisim/page/topics/automation-api.html)

## Shared examples

### Install and diagnose

```powershell
.\install.ps1
.\.install\gateway\Scripts\multisim-unified-mcp.exe --config .install\multisim.local.toml --doctor
```

### Manual install

Run each command in its matching environment / 在对应环境运行。

```powershell
# 64-bit gateway
python -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt
python -m pip install --no-deps .
# 32-bit Native
python -m pip install --no-deps --only-binary=:all: -r requirements/native.txt
# 32-bit COM
python -m pip install --no-deps --only-binary=:all: -r requirements/com.txt
# All three / 三个环境均执行
python -m pip check
```

### F5 input

```json
{
  "settle_time_s": 0.001,
  "cases": [
    {"name": "000", "inputs": {"A": 0, "B": 0, "Cin": 0}, "expected": {"S": 0, "Cout": 0}},
    {"name": "001", "inputs": {"A": 0, "B": 0, "Cin": 1}, "expected": {"S": 1, "Cout": 0}}
  ]
}
```

### COM input

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

### Tests

```powershell
python -m unittest discover -s tests -v
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

| Fixture / 夹具 | Requirements / 要求 |
| --- | --- |
| `--adder` | Full adder / 全加器 `.ms14` with switches / 开关 `A`, `B`, `Cin`; indicators / 指示灯 `S`, `Cout`; outputs / 电压输出 `V(OutProbe)`, `V(OutProbe1)`; free reference / 空闲位号 `U3`. |
| `--cases-dir` | Eight saved input combinations / 八种已保存的输入组合. Use / 文件名为 `E5_000.ms14`–`E5_111.ms14`, with the same voltage outputs / 电压输出名相同. |
| `--analog` (optional / 可选) | A wired analog `.ms14` with a source and ground for checking layout, netlist, image export, and numerical analysis. 用带电源和地的模拟电路检查布局、网表、导图及数值分析。 |
