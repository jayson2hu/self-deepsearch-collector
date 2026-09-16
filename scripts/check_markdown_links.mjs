#!/usr/bin/env node

import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LINK_PATTERN = /!?\[[^\]\r\n]*\]\(\s*(?<target><[^>\r\n]+>|[^\s)\r\n]+)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)/g;
const EXTERNAL_TARGET = /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i;

function walkMarkdown(directory) {
  const files = [];
  if (!existsSync(directory)) return files;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...walkMarkdown(candidate));
    else if (entry.isFile() && entry.name.toLowerCase().endsWith(".md")) files.push(candidate);
  }
  return files;
}

export function collectMarkdownFiles(repositoryRoot) {
  const rootFiles = readdirSync(repositoryRoot, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.toLowerCase().endsWith(".md"))
    .map((entry) => path.join(repositoryRoot, entry.name));
  return [...rootFiles, ...walkMarkdown(path.join(repositoryRoot, "docs"))]
    .sort((left, right) => left.localeCompare(right, "en"));
}

export function extractMarkdownTargets(markdown) {
  return [...markdown.matchAll(LINK_PATTERN)].map((match) => match.groups.target.replace(/^<|>$/g, ""));
}

function localPathPart(target) {
  if (EXTERNAL_TARGET.test(target)) return "";
  const withoutFragment = target.split("#", 1)[0];
  const withoutQuery = withoutFragment.split("?", 1)[0];
  if (!withoutQuery) return "";
  try {
    return decodeURIComponent(withoutQuery);
  } catch {
    return null;
  }
}

export function checkMarkdownLinks(repositoryRoot = ROOT) {
  const root = path.resolve(repositoryRoot);
  const files = collectMarkdownFiles(root);
  const missing = [];
  let localLinks = 0;

  for (const source of files) {
    const markdown = readFileSync(source, "utf8");
    for (const target of extractMarkdownTargets(markdown)) {
      const pathPart = localPathPart(target);
      if (pathPart === "") continue;
      localLinks += 1;
      if (pathPart === null) {
        missing.push({ source: path.relative(root, source), target, reason: "invalid_percent_encoding" });
        continue;
      }
      const resolved = pathPart.startsWith("/")
        ? path.resolve(root, pathPart.slice(1))
        : path.resolve(path.dirname(source), pathPart);
      const relative = path.relative(root, resolved);
      if (relative.startsWith("..") || path.isAbsolute(relative)) {
        missing.push({ source: path.relative(root, source), target, reason: "outside_repository" });
        continue;
      }
      if (!existsSync(resolved) || (!statSync(resolved).isFile() && !statSync(resolved).isDirectory())) {
        missing.push({ source: path.relative(root, source), target, reason: "missing" });
      }
    }
  }

  return {
    status: missing.length === 0 ? "passed" : "failed",
    markdown_files: files.length,
    local_links: localLinks,
    missing,
  };
}

export function main() {
  const report = checkMarkdownLinks();
  if (report.missing.length > 0) {
    for (const item of report.missing) {
      process.stderr.write(`- ${item.source} -> ${item.target} (${item.reason})\n`);
    }
    process.stderr.write(`Markdown link check failed: ${report.missing.length} invalid local link(s).\n`);
    process.exitCode = 1;
    return;
  }
  process.stdout.write(
    `Markdown link check passed: ${report.markdown_files} files, ${report.local_links} local links, 0 missing.\n`,
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
