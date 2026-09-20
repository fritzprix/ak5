#!/usr/bin/env node

const http = require("http");
const fs = require("fs");
const { spawn, exec } = require("child_process");
const path = require("path");

const HEALTH_URL = "http://127.0.0.1:8000/health";
const FRONTEND_DIR = path.resolve(__dirname, "..");

/** @type {import("child_process").ChildProcess | null} */
let backendProc = null;
let startedBackend = false;

function openBrowser(url) {
  const start =
    process.platform === "darwin"
      ? "open"
      : process.platform === "win32"
        ? "start"
        : "xdg-open";
  exec(`${start} ${url}`, () => {});
}

function findMonorepoRoot(startDir) {
  let dir = startDir;
  for (;;) {
    const rootPyproject = path.join(dir, "pyproject.toml");
    const backendPyproject = path.join(dir, "backend", "pyproject.toml");
    if (fs.existsSync(backendPyproject) || fs.existsSync(rootPyproject)) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

async function checkBackend(apiUrl = HEALTH_URL) {
  return new Promise((resolve) => {
    const req = http.get(apiUrl, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on("error", () => resolve(false));
    req.setTimeout(1500, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(url = HEALTH_URL, maxAttempts = 30) {
  for (let i = 0; i < maxAttempts; i++) {
    if (await checkBackend(url)) return true;
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

function resolveBackendCommand() {
  const repoRoot = findMonorepoRoot(FRONTEND_DIR) || findMonorepoRoot(process.cwd());
  if (repoRoot) {
    return {
      cmd: "uv",
      args: ["run", "ak5", "serve"],
      cwd: repoRoot,
      label: `uv run ak5 serve (cwd: ${repoRoot})`,
    };
  }
  // Published / npx context: no local pyproject — use uvx zero-install
  return {
    cmd: "uvx",
    args: ["ak5", "serve"],
    cwd: process.cwd(),
    label: "uvx ak5 serve",
  };
}

function startBackend() {
  return new Promise((resolve, reject) => {
    console.log("\x1b[33m⏳  Starting AK5 Backend Gateway...\x1b[0m");
    const { cmd, args, cwd, label } = resolveBackendCommand();
    console.log(`\x1b[36m   → ${label}\x1b[0m`);

    let settled = false;
    /** @type {ReturnType<typeof setTimeout> | undefined} */
    let timeout;
    const settle = (fn, value) => {
      if (settled) return;
      settled = true;
      if (timeout !== undefined) clearTimeout(timeout);
      fn(value);
    };

    backendProc = spawn(cmd, args, {
      cwd,
      stdio: "pipe",
      detached: false,
    });
    startedBackend = true;

    const onChunk = (data) => {
      const text = data.toString();
      if (
        text.includes("Running on") ||
        text.includes("Started server") ||
        text.includes("Uvicorn running")
      ) {
        console.log("\x1b[32m✓  AK5 Backend Gateway process reported ready\x1b[0m");
        settle(resolve);
      }
    };

    backendProc.stdout?.on("data", onChunk);
    backendProc.stderr?.on("data", onChunk);

    timeout = setTimeout(() => {
      settle(reject, new Error("Backend startup timeout (30s)"));
    }, 30000);

    backendProc.on("error", (err) => settle(reject, err));
    backendProc.on("exit", (code, signal) => {
      if (!settled) {
        settle(reject, new Error(`Backend exited early (code=${code}, signal=${signal})`));
      }
    });
  });
}

function shutdownBackend() {
  if (!backendProc || backendProc.killed) return;
  try {
    backendProc.kill("SIGTERM");
  } catch {
    // ignore
  }
  backendProc = null;
}

function resolveNextCommand() {
  if (process.env.AK5_NEXT_CMD === "dev" || process.env.AK5_NEXT_CMD === "start") {
    return process.env.AK5_NEXT_CMD;
  }
  const hasBuild = fs.existsSync(path.join(FRONTEND_DIR, ".next", "BUILD_ID"));
  return hasBuild ? "start" : "dev";
}

function installSignalHandlers(nextProc) {
  const cleanup = () => {
    if (nextProc && !nextProc.killed) {
      try {
        nextProc.kill("SIGTERM");
      } catch {
        // ignore
      }
    }
    if (startedBackend) {
      shutdownBackend();
    }
  };

  process.on("SIGINT", () => {
    cleanup();
    process.exit(0);
  });
  process.on("SIGTERM", () => {
    cleanup();
    process.exit(0);
  });
  process.on("exit", () => {
    if (startedBackend) shutdownBackend();
  });
}

async function main() {
  console.log("\x1b[36m%s\x1b[0m", "==================================================");
  console.log("\x1b[1m\x1b[35m%s\x1b[0m", "🎯 AK5 (Agent K5) — Agent-Orchestrated Kanban UI");
  console.log("\x1b[36m%s\x1b[0m", "==================================================");

  const isBackendAlive = await checkBackend(HEALTH_URL);

  if (!isBackendAlive) {
    console.log("\x1b[33m⚠️  AK5 Backend Gateway is not running.\x1b[0m");
    console.log("\x1b[32m💡  Auto-starting backend...\x1b[0m");
    try {
      await startBackend();
      const healthy = await waitForBackend(HEALTH_URL, 30);
      if (!healthy) {
        throw new Error("Backend process started but /health did not become ready");
      }
      console.log("\x1b[32m✓  AK5 Backend Gateway is healthy\x1b[0m");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.log("\x1b[31m❌  Failed to start backend: " + message + "\x1b[0m");
      console.log("\x1b[33m   Please start it manually: uv run ak5 serve  (or: uvx ak5 serve)\x1b[0m");
    }
  } else {
    console.log("\x1b[32m✓  Connected to AK5 Gateway (http://127.0.0.1:8000)\x1b[0m");
  }

  const port = process.env.PORT || 3000;
  const command = resolveNextCommand();
  console.log(`🚀 Starting AK5 Web Dashboard on http://localhost:${port} (next ${command})...`);

  const isWindows = process.platform === "win32";
  const npxCmd = isWindows ? "npx.cmd" : "npx";

  const nextProc = spawn(npxCmd, ["next", command, "-p", String(port)], {
    cwd: FRONTEND_DIR,
    stdio: "inherit",
    env: { ...process.env, PORT: String(port) },
  });

  installSignalHandlers(nextProc);

  setTimeout(() => {
    openBrowser(`http://localhost:${port}`);
  }, 2000);

  nextProc.on("close", (code) => {
    if (startedBackend) shutdownBackend();
    process.exit(code || 0);
  });
}

main().catch(console.error);
