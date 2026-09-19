#!/usr/bin/env node

const http = require("http");
const { spawn, exec } = require("child_process");
const path = require("path");

function openBrowser(url) {
  const start =
    process.platform === "darwin"
      ? "open"
      : process.platform === "win32"
      ? "start"
      : "xdg-open";
  exec(`${start} ${url}`, () => {});
}

async function checkBackend(apiUrl = "http://127.0.0.1:8000/health") {
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

async function main() {
  console.log("\x1b[36m%s\x1b[0m", "==================================================");
  console.log("\x1b[1m\x1b[35m%s\x1b[0m", "🎯 AK5 (Agent K5) — Agent-Orchestrated Kanban UI");
  console.log("\x1b[36m%s\x1b[0m", "==================================================");

  const isBackendAlive = await checkBackend();
  if (!isBackendAlive) {
    console.log("\x1b[33m%s\x1b[0m", "⚠️  AK5 Backend Gateway is not running at http://127.0.0.1:8000.");
    console.log("\x1b[32m%s\x1b[0m", "💡 Tip: You can start the backend gateway in another terminal with:");
    console.log("\x1b[1m\x1b[36m%s\x1b[0m", "   uvx ak5 serve");
    console.log("\x1b[36m%s\x1b[0m", "--------------------------------------------------");
  } else {
    console.log("\x1b[32m%s\x1b[0m", "✓ Connected to AK5 Gateway (http://127.0.0.1:8000)");
  }

  const port = process.env.PORT || 3000;
  console.log(`🚀 Starting AK5 Web Dashboard on http://localhost:${port}...`);

  const appDir = path.resolve(__dirname, "..");
  const isWindows = process.platform === "win32";
  const npxCmd = isWindows ? "npx.cmd" : "npx";

  // Check if .next exists (built)
  const fs = require("fs");
  const isBuilt = fs.existsSync(path.join(appDir, ".next"));
  const command = isBuilt ? "start" : "dev";

  const nextProc = spawn(npxCmd, ["next", command, "-p", String(port)], {
    cwd: appDir,
    stdio: "inherit",
    env: { ...process.env, PORT: String(port) },
  });

  // Open browser after 2 seconds
  setTimeout(() => {
    openBrowser(`http://localhost:${port}`);
  }, 2000);

  nextProc.on("close", (code) => {
    process.exit(code || 0);
  });
}

main().catch(console.error);
