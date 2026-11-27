# Multisim MCP for DeepSeek Harness

**简体中文** · [English](#english)

在 DeepSeek Harness 中连接本机 Multisim MCP，编辑原理图、规划布线并运行仿真。需要 Windows，适用于 Harness 0.2.2-rc.5 至 0.3.0 之前的版本。

## 安装与连接

1. 在包含本适配器的同一份仓库源码中，按 [QUICKSTART.md](https://github.com/Arthyra/multisim-mcp/blob/main/QUICKSTART.md) 运行 `install.ps1` 并确认网关诊断通过。记下安装目录，默认是仓库下的 `.install`。该目录应包含 `multisim.local.toml` 和 `gateway/Scripts/multisim-unified-mcp.exe`。
2. 从 [v0.7.1 Release](https://github.com/Arthyra/multisim-mcp/releases/tag/v0.7.1) 下载 `dsh-multisim-mcp-0.1.0.tgz`，或在仓库根目录运行下方的 `npm pack`。在桌面端打开「插件 → 添加插件」，输入 `.tgz` 的绝对路径。Web 端可用下方的 `dsh plugin` 命令安装，按实际位置替换文件路径。
3. 打开插件配置，将 `installDir` 设置为第 1 步的绝对路径。也可在 Web profile 的 `cordis.patch.yml` 中配置。保存后，用 `mcp__multisim__gateway_status` 检查 Native 和 COM 状态。

```powershell
npm pack ./integrations/deepseek-harness --pack-destination $env:TEMP
dsh plugin --profile web add (Join-Path $env:TEMP 'dsh-multisim-mcp-0.1.0.tgz')
```

```yaml
- id: multisim-mcp
  config:
    installDir: 'C:\MultisimMCP'
```

留空 `installDir` 时，插件保持断开。停用或卸载插件会撤下它注册的工具。若手动编辑过 `cordis.patch.yml`，卸载时也删掉对应配置。MCP 工具调用默认允许 10 分钟，可用 `toolCallTimeoutMs` 调整。

需要已安装并激活的受支持版本 Multisim，以及主项目安装器要求的 64 位和 32 位 Python。插件使用本地 stdio 进程。安装插件后，Multisim 和网关依赖仍由用户通过主项目安装器配置。

## English

Connect DeepSeek Harness to a local Multisim MCP installation for schematic editing, layout, routing, and simulation. The host must run on Windows with Harness 0.2.2-rc.5 or later, below 0.3.0.

1. From the same checkout that contains this adapter, run `install.ps1` as described in the main project's [QUICKSTART.md](https://github.com/Arthyra/multisim-mcp/blob/main/QUICKSTART.md#english) and check the gateway diagnosis. Note the install directory, which defaults to `.install` under the repository. It should contain `multisim.local.toml` and `gateway/Scripts/multisim-unified-mcp.exe`.
2. Download `dsh-multisim-mcp-0.1.0.tgz` from the [v0.7.1 release](https://github.com/Arthyra/multisim-mcp/releases/tag/v0.7.1), or run `npm pack` from the repository root as shown above. In Harness Desktop, open **Plugins → Add plugin** and enter the absolute path of the `.tgz`. For Web, use the `dsh plugin` command above with the location of your file.
3. Set `installDir` to the absolute directory from step 1 in the plugin settings. For Web, you can also use the YAML above in the profile's `cordis.patch.yml`. Check the Native and COM backends with `mcp__multisim__gateway_status`.

An empty `installDir` leaves Multisim disconnected. Disabling or uninstalling the plugin withdraws its tools. If you edited `cordis.patch.yml` by hand, remove that entry when uninstalling. The default tool-call timeout is ten minutes and can be changed with `toolCallTimeoutMs`.

The gateway requires a supported, activated Multisim installation and the 64-bit and 32-bit Python environments specified in the project's installation guide. The plugin connects over local stdio. Multisim and its gateway dependencies are set up through the main project.
