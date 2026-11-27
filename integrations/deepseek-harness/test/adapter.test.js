import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rmdir, unlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { apply, Config } from '../index.js'

test('an empty install directory does not load the MCP bridge', async () => {
  await apply({}, Config({}))
})

test('a configured install requires Windows', { skip: process.platform === 'win32' }, async () => {
  await assert.rejects(apply({}, Config({ installDir: 'C:\\MultisimMCP' })), /requires a Windows/)
})

test('uses the installed gateway and passes paths with spaces as separate arguments', {
  skip: process.platform !== 'win32',
}, async (t) => {
  for (const installDir of ['relative/path', '\\drive-relative']) {
    await assert.rejects(apply({}, Config({ installDir })), /absolute InstallDir/)
  }

  const installDir = await mkdtemp(join(tmpdir(), 'Multisim 测试 '))
  const gatewayDir = join(installDir, 'gateway')
  const scriptsDir = join(gatewayDir, 'Scripts')
  const command = join(scriptsDir, 'multisim-unified-mcp.exe')
  const configFile = join(installDir, 'multisim.local.toml')
  await mkdir(scriptsDir, { recursive: true })
  t.after(async () => {
    for (const file of [command, configFile]) {
      await unlink(file).catch(error => { if (error.code !== 'ENOENT') throw error })
    }
    for (const dir of [scriptsDir, gatewayDir, installDir]) await rmdir(dir)
  })

  const config = Config({ installDir })
  await writeFile(configFile, '')
  await assert.rejects(apply({}, config), /gateway executable is missing/)
  await unlink(configFile)
  await writeFile(command, '')
  await assert.rejects(apply({}, config), /multisim\.local\.toml is missing/)
  await writeFile(configFile, '')

  const bridge = {}
  const ctx = {
    loader: { import: async (name) => {
      assert.equal(name, '@deepseek-ai/dsh-mcp-client')
      return bridge
    } },
    plugin: t.mock.fn(),
  }
  await apply(ctx, config)
  assert.equal(ctx.plugin.mock.callCount(), 1)
  assert.deepEqual(ctx.plugin.mock.calls[0].arguments, [bridge, {
    serverName: 'multisim',
    transport: 'stdio',
    command,
    args: ['--config', configFile],
    toolCallTimeoutMs: 600000,
  }])
})
