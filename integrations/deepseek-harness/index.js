import { stat } from 'node:fs/promises'
import { win32 as path } from 'node:path'
import Schema from '@deepseek-ai/schemastery'

export const name = 'multisim-mcp'
export const inject = ['loader', 'tools']

export const Config = Schema.object({
  installDir: Schema.string().default('').description(
    'Absolute InstallDir used by the multisim-mcp installer. Leave empty to keep Multisim disconnected.',
  ),
  toolCallTimeoutMs: Schema.natural().min(1000).default(600000).description(
    'Multisim tool-call timeout in milliseconds.',
  ),
})

async function requireFile(file, label) {
  const info = await stat(file, { throwIfNoEntry: false })
  if (!info?.isFile()) {
    throw new Error(`Multisim MCP: ${label} is missing. Select the InstallDir created by install.ps1.`)
  }
}

export async function apply(ctx, config) {
  const installDir = config.installDir.trim()
  if (!installDir) return
  if (process.platform !== 'win32') {
    throw new Error('Multisim MCP requires a Windows Harness host.')
  }
  if (path.parse(installDir).root.length < 3) {
    throw new Error('Multisim MCP: enter the absolute InstallDir created by install.ps1.')
  }

  const command = path.join(installDir, 'gateway', 'Scripts', 'multisim-unified-mcp.exe')
  const gatewayConfig = path.join(installDir, 'multisim.local.toml')
  await Promise.all([
    requireFile(command, 'the gateway executable'),
    requireFile(gatewayConfig, 'multisim.local.toml'),
  ])

  // Resolve the host's bridge so it shares the host's tool registry and lifecycle.
  const bridge = await ctx.loader.import('@deepseek-ai/dsh-mcp-client')
  await ctx.plugin(bridge, {
    serverName: 'multisim',
    transport: 'stdio',
    command,
    args: ['--config', gatewayConfig],
    toolCallTimeoutMs: config.toolCallTimeoutMs,
  })
}
