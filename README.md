# multisim-mcp

**简体中文** · [English](README.en.md)

一个非官方的 NI Multisim 本地 MCP 集成。让 AI 助手搭建和修改原理图、操控交互仿真，并用 Multisim 实测结果核对电路。

[打开快速开始](QUICKSTART.md#简体中文)

## 能做什么

- **画图并检查原理图。** 从 Multisim 主数据库查找指定型号，放置元件、接线并保存为可编辑的 `.ms14`。用原生网表检查电气连接，再单独检查图面碰撞，必要时导出电路图。
- **整理整张图的摆位和布线。** 按原生网络推断成组器件，也可手动分组、锁定器件位置。工具比较器件摆位、共同走线通道和供电主线。应用前可审阅方案，应用后核对端子连接和新增图面问题，并可导出同尺度前后图。操作见[原理图排版与布线](QUICKSTART.md#原理图排版与布线)。
- **自动核对数字电路。** 运行 Multisim 的交互仿真（手动启动的快捷键是 F5），逐组拨动开关，在指定仿真时间后读取实际灯态并对照真值表，最后恢复开关原位。
- **测电压、看波形。** 优先用 Native 后端运行直流工作点、瞬态或交流分析并读取结果。需要 COM 专用能力、跨后端复核或明确要求用 COM 时，可以把当前工程保存后交给 COM，网关会核对两个后端操作的是同一份文件。

## 兼容性与限制

- 实机支持 64 位 Windows 上的 32 位 NI Multisim `14.3.3.0`，其中 `multisim.exe` 的文件版本为 `14.3.1500.0`。安装器检查文件版本，Native 运行前校验关键模块的指纹。补丁和发行版的验证范围见[兼容性矩阵](QUICKSTART.md#multisim-143-compatibility)。
- 自动拨开关和读灯支持 `INTERACTIVE_DIGITAL_CONSTANT` 与 `PROBE_DIG_RED`。真值表工具按固定仿真时间采样，时序特性需单独验证。
- 布线规划会比较有限的候选方案。建议在工程副本上审阅结果，检查复杂反馈、电源和人工布线区域。
- COM 数值分析通过 Windows 自动化接口执行。保存用例的逻辑判定适用于输入保持固定的组合电路。Native 和 COM 各自在独立进程中运行，通过一个 stdio MCP 入口接入。

## 文档与上游

在 PowerShell 中运行 `./install.ps1`，再将生成的 stdio 配置添加到 MCP 客户端。[QUICKSTART.md](QUICKSTART.md#简体中文)包含安装条件、诊断和验证步骤。[AGENTS.md](AGENTS.md)说明如何检查电路和保护原工程。

[Multisim Native MCP v0.5.2](https://github.com/y-wi/multisim-native-mcp/tree/v0.5.2) 提供原生编辑、检查和仿真。[Multisim MCP v1.3.1](https://github.com/yxy050208/multisim-mcp/tree/v1.3.1) 提供 COM 数值分析。本项目在两者之上增加整图排版与布线、交互仿真的逐行真值表判定，以及同一文件的工程交接。

## 许可证

网关采用 [MIT 许可](LICENSE)。使用时需要自行安装并激活 Multisim。Frida、非公开接口和上游依赖见[第三方声明](THIRD_PARTY_NOTICES.md)。
