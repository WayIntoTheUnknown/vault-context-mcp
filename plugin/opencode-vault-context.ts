import type { Plugin } from "@opencode-ai/plugin"
import { spawn } from "node:child_process"
import { appendFileSync, mkdirSync, readFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

type VaultCtxConfig = {
  enabled?: boolean
  python?: string
  vault?: string
  aiDir?: string
  debounceMs?: number
}

const DEFAULT_DEBOUNCE_MS = 3000

function configDir(): string {
  return process.env.XDG_CONFIG_HOME
    ? join(process.env.XDG_CONFIG_HOME, "opencode")
    : join(homedir(), ".config", "opencode")
}

function loadConfig(): VaultCtxConfig {
  try {
    return JSON.parse(readFileSync(join(configDir(), "vault-ctx.json"), "utf8")) as VaultCtxConfig
  } catch {
    return {}
  }
}

function logFile(): string {
  const base = process.env.LOCALAPPDATA ?? join(homedir(), ".local", "state")
  return join(base, "vault-ctx", "vault-ctx.log")
}

function log(message: string): void {
  try {
    const file = logFile()
    mkdirSync(dirname(file), { recursive: true })
    appendFileSync(file, `${new Date().toISOString()} plugin ${message}\n`, "utf8")
  } catch {
    // never fail the agent because of logging
  }
}

export const VaultContextPlugin: Plugin = async () => {
  const config = loadConfig()
  const disabled = process.env.VAULT_CTX_DISABLE === "1" || config.enabled === false
  const python = process.env.VAULT_CTX_PYTHON || config.python || "python"
  const debounceMs = Number(process.env.VAULT_CTX_DEBOUNCE_MS || config.debounceMs || DEFAULT_DEBOUNCE_MS)
  const timers = new Map<string, ReturnType<typeof setTimeout>>()

  const exportSession = (sessionID: string) => {
    const env: Record<string, string> = { ...process.env } as Record<string, string>
    if (config.vault) env.VAULT_PATH = config.vault
    if (config.aiDir) env.VAULT_CTX_AI_DIR = config.aiDir
    log(`export start ${sessionID}`)
    const child = spawn(python, ["-m", "vault_ctx", "export", "--session", sessionID, "--quiet"], {
      env,
      windowsHide: true,
      stdio: "ignore",
    })
    child.on("error", (error) => log(`export spawn error ${sessionID}: ${error.message}`))
    child.on("exit", (code) => {
      if (code !== 0) log(`export failed ${sessionID}: exit ${code}`)
    })
  }

  const schedule = (sessionID: string) => {
    const existing = timers.get(sessionID)
    if (existing) clearTimeout(existing)
    timers.set(
      sessionID,
      setTimeout(() => {
        timers.delete(sessionID)
        exportSession(sessionID)
      }, debounceMs),
    )
  }

  return {
    event: async ({ event }) => {
      if (disabled) return
      const payload = event as unknown as {
        type?: string
        properties?: { sessionID?: string; info?: { role?: string; time?: { completed?: number } } }
        data?: { sessionID?: string }
      }
      const sessionID = payload.properties?.sessionID ?? payload.data?.sessionID
      if (!sessionID) return
      const assistantFinished =
        payload.type === "message.updated" &&
        payload.properties?.info?.role === "assistant" &&
        Boolean(payload.properties?.info?.time?.completed)
      if (payload.type === "session.idle" || assistantFinished) {
        schedule(sessionID)
      }
    },
  }
}

export default VaultContextPlugin
