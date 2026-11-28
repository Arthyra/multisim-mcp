import assert from 'node:assert/strict'
import { rename, writeFile } from 'node:fs/promises'
import { setTimeout } from 'node:timers/promises'

export const inject = ['loader', 'tools']

export async function apply(ctx) {
  const output = process.env.MULTISIM_DSH_PROBE_RESULT
  const prefix = 'mcp__multisim__'
  const statusTool = `${prefix}gateway_status`
  let result
  try {
    const deadline = Date.now() + 30_000
    while (!ctx.tools.get(statusTool) && Date.now() < deadline) await setTimeout(200)
    const tool = ctx.tools.get(statusTool)
    assert.ok(tool, 'Harness did not discover gateway_status')
    const response = await tool.execute({}, { signal: AbortSignal.timeout(10_000) })
    assert.ok(!response.isError, 'Harness received an MCP tool error')
    const status = response.structuredContent ?? JSON.parse(response.content[0].text)
    // This smoke test launches the real gateway without Multisim backends.
    assert.equal(status.native.available, false)
    assert.equal(status.com.available, false)

    const adapter = [...ctx.loader.entries()].find(entry => entry.options.name === 'dsh-multisim-mcp')
    assert.ok(adapter?.fiber, 'Harness did not mount the Multisim adapter')
    await adapter.fiber.dispose()
    assert.ok(!ctx.tools.definitions().some(tool => tool.name.startsWith(prefix)),
      'Multisim tools remained registered after unload')
    result = { ok: true }
  } catch (error) {
    result = { ok: false, error: String(error) }
  }
  await writeFile(`${output}.tmp`, JSON.stringify(result))
  await rename(`${output}.tmp`, output)
}
