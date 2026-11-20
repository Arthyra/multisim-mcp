# multisim-mcp

**简体中文** · [English](README.en.md)

让 AI 助手通过本地 MCP 在 NI Multisim 中搭建和修改原理图、操控交互仿真，并用 Multisim 实测结果核对电路。

[打开快速开始](QUICKSTART.md#简体中文)

## 能做什么

- **画图并检查原理图。** 从 Multisim 主数据库查找指定型号，放置元件、接线并保存为可编辑的 `.ms14`。用原生网表检查电气连接，再单独检查图面碰撞，必要时导出电路图。
- **自动核对数字电路。** 运行 Multisim 的交互仿真（手动启动的快捷键是 F5），逐组拨动开关，在指定仿真时间后读取实际灯态并对照真值表，最后恢复开关原位。
- **测电压、看波形。** 优先用 Native 后端运行直流工作点、瞬态或交流分析并读取结果。需要 COM 专用能力、跨后端复核或明确要求用 COM 时，可以把当前工程保存后交给 COM，网关会核对两个后端操作的是同一份文件。

## 兼容性与限制

- 实机验证环境为 Windows 上的 32 位 NI Multisim `14.3.1500.0`。Native 固定为 `0.5.2`，COM 固定为 `1.3.1`。标准安装使用一个 64 位网关和两个 32 位 CPython 3.12 后端环境。其他 Multisim 构建未经验证，Native 会检查版本和关键模块。
- 自动拨开关和读灯支持 `INTERACTIVE_DIGITAL_CONSTANT` 与 `PROBE_DIG_RED`。真值表工具按固定仿真时间采样，时序特性需单独验证。
- COM 数值分析通过 Windows 自动化接口执行。保存用例的逻辑判定适用于输入保持固定的组合电路。Native 和 COM 各自在独立进程中运行，通过一个 stdio MCP 入口接入。

## 文档与上游

项目处于预览阶段。[QUICKSTART.md](QUICKSTART.md#简体中文)包含安装、诊断与验证步骤。[AGENTS.md](AGENTS.md)说明如何检查电路和保护原工程。

[Multisim Native MCP](https://github.com/y-wi/multisim-native-mcp) 提供原生编辑、检查和仿真。[Multisim MCP](https://github.com/yxy050208/multisim-mcp) 提供 COM 数值分析。本项目增加了交互仿真的逐行真值表判定，以及同一文件的工程交接。

## 许可证

网关采用 [MIT 许可](LICENSE)。使用时需要自行安装并激活 Multisim。Frida、非公开接口和上游依赖见[第三方声明](THIRD_PARTY_NOTICES.md)。
