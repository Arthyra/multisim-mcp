# multisim-mcp

**简体中文** · [English](README.en.md)

一个连接本机 NI Multisim 的 MCP 服务，支持编辑原生原理图、在 F5 模式下测试数字逻辑，以及对已保存的电路运行 COM 仿真。客户端通过一个 stdio 入口使用全部功能。本项目是独立的互操作工具。

当前预览版为 [v0.5.0](#v050-预览版)。首次使用请看[快速开始](#快速开始)。

## 功能

| 工作流程 | 支持的操作 |
| --- | --- |
| 原理图编辑 | 打开或新建 `.ms14`，搜索 Multisim 主数据库，放置原生元件、连接引脚、标注网络并保存。 |
| F5 交互仿真 | 启停仿真，设置受支持的逻辑开关，读取指示灯实际状态，并按仿真时间验证整张真值表。 |
| 已保存电路仿真 | 将同一份已保存工程交给 COM，运行直流、交流或瞬态分析，批量验证已保存的逻辑用例。 |

## 安装要求

- Windows 上已安装并激活 NI Multisim 14.3。安装器针对实测的 32 位构建 `14.3.1500.0`。
- 网关使用 64 位 Python 3.11 或更新版本，两个后端使用 32 位 CPython 3.12。[Python Windows 下载页](https://www.python.org/downloads/windows/)
- 安装时需要联网下载固定版本的[原生后端 v0.5.2](https://github.com/y-wi/multisim-native-mcp/releases/tag/v0.5.2)、[COM 后端 v1.3.1](https://pypi.org/project/multisim-mcp/1.3.1/)及其依赖。

## 快速开始

克隆或下载仓库，在仓库目录打开 PowerShell，运行安装脚本。

```powershell
.\install.ps1
```

安装器默认使用 `python` 创建 64 位网关环境，并通过 `py -3.12-32` 查找 32 位解释器。也可以用 `-Python64` 和 `-Python32` 指定对应的 Python 可执行文件。如果 Multisim 安装在自定义位置，用 `-MultisimPath` 指定包含 `multisim.exe` 的目录。`-InstallDir` 可以指定其他安装目录。

脚本默认在 `.install` 下创建三个环境，并生成两份本地配置。每套环境按 [requirements](requirements/) 中的固定版本安装依赖，再运行 `pip check`。Codex 用户将生成的 `multisim.client.toml` 配置段加入客户端配置。其他 stdio MCP 客户端使用同一个可执行文件和参数，按客户端格式配置。已有的 `multisim.local.toml` 会被保留。

启动 MCP 后，可以调用 `native_runtime_status` 和 `com_runtime_status` 检查本地运行环境。需要将 `.ms14` 解码为 XML 时，参照 COM 后端的[可选编解码器安装说明](https://github.com/yxy050208/multisim-mcp/blob/v1.3.1/mcp_server/README.md#install)。

### 手动安装

`install.ps1` 用于简化环境准备。安装完成后，客户端直接启动 `multisim-unified-mcp`。

手动安装时，先创建三个 Python 环境，再分别安装 [requirements](requirements/) 中对应的锁定依赖。在网关环境中运行 `python -m pip install --no-deps --only-binary=:all: -r requirements/gateway.txt` 和 `python -m pip install --no-deps .`。两个 32 位环境分别安装 `native.txt` 和 `com.txt` 中的依赖。每套环境装好后运行 `python -m pip check`。

将 [multisim.example.toml](multisim.example.toml) 复制为 `multisim.local.toml`，填入各后端 Python 的绝对路径。Multisim 使用自定义安装目录时，在 `[native.env]` 中设置 `MULTISIM_NATIVE_INSTALL`。配置客户端启动网关，并用 `--config` 传入该 TOML 文件的绝对路径。

### 安装诊断

```powershell
.\.install\gateway\Scripts\multisim-unified-mcp.exe --config .install\multisim.local.toml --doctor
```

`--doctor` 检查 Python 位数、所需模块、后端启动、Multisim 构建版本，以及 COM 工作进程的兼容性和注册状态。命令输出不含本机路径的 JSON 报告，并以只读方式检查运行环境。软件激活在 Multisim 中完成。

连接 MCP 后，用 `gateway_status` 查看两个后端的状态。某个后端启动失败或连接中断时，另一个后端仍可提供服务。修复故障后端的安装或配置后，重启网关即可重新加载。

## 第一个电路

1. 复制一份自己的 `.ms14` 工程。调用 `native_open_circuit`，传入 `{"source_path":"ABSOLUTE_PATH_TO_YOUR_CIRCUIT.ms14"}`，将占位符替换为工程的绝对路径。需要空白原理图时，用 `native_create_circuit`。
2. 调用 `native_search_library` 并传入 `{"query":"74LS153N"}` 搜索元件。这个型号可选择 `TTL / 74LS` 条目。将返回的型号、组和系列传给 `native_place_component`，在图纸空白处放置，并根据返回的引脚名接线。仿真原电路前，移除用于试放且尚未接线的元件。
3. 工程带有受支持的开关和指示灯时，可以进行 [F5 真值表验证](#验证-f5-数字逻辑)。需要对已保存电路做数值仿真时，继续第 4 步。
4. 将当前 `.ms14` 的绝对路径传给 `handoff_to_com`。交接成功后，先调用 `com_enum_outputs`，再将返回的电压信号作为 `output_name` 传给 `com_run_transient`。恢复原理图编辑前，先断开 COM 会话。

网关保留上游工具的参数和结果格式，并按顺序执行工具调用。启动 F5 的请求返回后，仿真仍可继续运行。两个后端各自管理自己的 Multisim 会话。

### 将工程交给 COM

调用 `handoff_to_com` 并传入 `{"path":"ABSOLUTE_PATH_TO_ADDER.ms14"}`，路径应指向当前打开的工程。网关先核对工程路径，再停止 F5、保存、释放原生会话，随后连接 COM 并打开保存后的文件。交接期间，网关按顺序执行这些操作。

检查返回结果中的 `ok` 和 `stage`。成功时，`stage` 为 `"complete"`，并返回 `saved_path` 和 `opened_path`。任一后端不可达时，交接会在 `check_backends` 阶段结束，原生工程保持当前状态。后续步骤失败时，结果会说明失败阶段以及原生会话是否已释放。如果保存成功而 COM 打开失败，可以使用返回的 `saved_path` 重试。

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

### 批量验证已保存的逻辑用例

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

### 连接现有原理图窗口

原生后端在查找编辑器时会跳过带 `/SimServer` 参数的无界面进程。仿真器和 COM 仍可使用这些进程。如果打开了多个原理图编辑器，用 `native_attach` 指定进程 ID 和工程索引。连接后可用 `native_list_projects` 查看工程及其索引，索引 0 对应该进程的第一份工程。

## 已测试的配置

- Windows 上的 32 位 Multisim `14.3.1500.0`，原生后端 `v0.5.2`，COM 后端 `v1.3.1`。
- 完整集成测试使用 64 位 Python 3.13 运行网关，两个后端均使用 32 位 CPython 3.12。
- F5 逻辑操作使用原生 `INTERACTIVE_DIGITAL_CONSTANT` 开关和 `PROBE_DIG_RED` 指示灯。

原生编辑和 F5 交互依赖 Multisim 对应版本的内部实现。许可和互操作说明见[第三方声明](THIRD_PARTY_NOTICES.md)。

## 回归测试

在网关 Python 环境中运行 `python -m unittest discover -s tests -p test_backend_isolation.py`，测试使用本地进程模拟后端启动失败和连接中断。

运行完整集成测试前，按前文安装并配置后端，关闭 Multisim 原理图编辑窗口，以便测试独立启动。脚本使用你提供的工程的临时副本。

- `--adder` 指向一份全加器 `.ms14`。工程包含 `A`、`B`、`Cin` 原生逻辑开关、`S` 和 `Cout` 红色逻辑指示灯，以及 `V(OutProbe)` 和 `V(OutProbe1)` 两个电压输出。请保留未使用的 `U3` 位号，供测试临时放件。
- `--cases-dir` 指向八个已保存的原生用例，文件名为 `E5_000.ms14` 至 `E5_111.ms14`。每份用例使用相同的两个电压输出名，并设置与文件名对应的初始输入状态。

```powershell
.\.install\gateway\Scripts\python.exe tests\smoke_multisim.py --config .install\multisim.local.toml --adder $adderFile --cases-dir $caseDirectory
```

将 `$adderFile` 和 `$caseDirectory` 设为本机测试数据。测试覆盖 `/SimServer` 运行时打开工程、主库放件、F5 八组真值表、交接路径核对和 COM 实测，其中包括故意填错预期值的用例。运行此测试需要本机已授权的 Multisim 和自备测试工程。

## v0.5.0 预览版

- 新增 `native_verify_f5_cases`。它逐组设置输入，按仿真时间采样实际灯态，返回每行判定，并恢复原开关状态。
- 实测通过全加器 F5 八组输入、同一工程交接及 COM 八组用例。两种验证方式都正确判定了故意填错的预期值。
- 提供锁定依赖的三环境安装器、独立诊断和后端故障隔离。

## 许可证

网关使用 [MIT 许可](LICENSE)。安装器从上游发布源获取固定版本的后端。使用者自行准备已授权的 Multisim 和测试工程。[第三方声明](THIRD_PARTY_NOTICES.md)
