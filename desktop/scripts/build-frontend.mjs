import { spawnSync } from "node:child_process";
for (const [entry, args] of [
  ["scripts/extract-ui-source.mjs", ["--check"]],
  ["scripts/build-ui-locales.mjs", ["--check"]],
  ["node_modules/typescript/bin/tsc", ["--noEmit"]],
  ["node_modules/vite/bin/vite.js", ["build"]],
]) {
  const result = spawnSync(process.execPath, [entry, ...args], { stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
