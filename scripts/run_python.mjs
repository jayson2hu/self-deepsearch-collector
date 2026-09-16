#!/usr/bin/env node

import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function pythonCandidates(root = ROOT, platform = process.platform, env = process.env) {
  const candidates = [];
  if (env.PYTHON?.trim()) candidates.push(env.PYTHON.trim());
  if (platform === "win32") candidates.push(path.join(root, ".venv", "Scripts", "python.exe"));
  else candidates.push(path.join(root, ".venv", "bin", "python"));
  candidates.push(platform === "win32" ? "python" : "python3");
  return candidates;
}

export function resolvePython(root = ROOT, platform = process.platform, env = process.env) {
  for (const candidate of pythonCandidates(root, platform, env)) {
    if (path.isAbsolute(candidate) && !existsSync(candidate)) continue;
    return candidate;
  }
  throw new Error("找不到 Python；请设置 PYTHON，或在仓库 .venv 中安装 Python 3.12");
}

export function main(argv = process.argv.slice(2)) {
  if (argv.length === 0) throw new Error("用法：node scripts/run_python.mjs <script.py> [参数...]");
  const result = spawnSync(resolvePython(), argv, { cwd: ROOT, env: process.env, stdio: "inherit", windowsHide: true });
  if (result.error) throw result.error;
  process.exitCode = result.status ?? 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 1;
  }
}
