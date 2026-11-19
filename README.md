# multisim-mcp

**简体中文** · [English](README.en.md)

一个连接本机 NI Multisim 的 MCP 服务。通过同一个 stdio 入口，可以编辑原生原理图、在 F5 模式下验证数字逻辑，并对已保存的工程运行 COM 仿真。

本项目参考并使用两个上游作为后端。[multisim-native-mcp](https://github.com/y-wi/multisim-native-mcp) 提供原生原理图与 F5 操作，[Multisim MCP](https://github.com/yxy050208/multisim-mcp) 提供 COM 仿真。

## 快速开始

[打开快速开始](QUICKSTART.md#简体中文)

## 功能

| 工作流程 | 支持的操作 |
| --- | --- |
| 原理图编辑 | 新建或打开 `.ms14`，从主数据库放置原生元件、连接引脚、标注网络并保存。 |
| F5 交互仿真 | 设置逻辑开关，读实际灯态，按仿真时间验证整张真值表。 |
| COM 仿真 | 打开交接的同一份工程，运行直流、交流或瞬态分析，批量验证已保存的逻辑用例。 |

## 已测试的配置

Windows 上的 32 位 Multisim `14.3.1500.0`，原生后端 `v0.5.2`，COM 后端 `v1.3.1`。完整回归使用 64 位 Python 3.13 运行网关，两个后端均使用 32 位 CPython 3.12。F5 逻辑操作支持实测的 `INTERACTIVE_DIGITAL_CONSTANT` 和 `PROBE_DIG_RED`。

## v0.5.0 预览版

- 新增 `native_verify_f5_cases`，按仿真时钟逐行采样并恢复原开关状态。
- 实测通过全加器 F5 八组输入、同一工程交接和 COM 八组用例。两种验证均能识别错误的预期值。
- 提供锁定依赖的三环境安装器、独立诊断和后端故障隔离。

## 许可证

网关使用 [MIT 许可](LICENSE)。本项目是独立的互操作工具，用户自行准备已授权的 Multisim 和测试工程。原生编辑和 F5 交互依赖特定版本的内部实现，相关说明见[第三方声明](THIRD_PARTY_NOTICES.md)。
