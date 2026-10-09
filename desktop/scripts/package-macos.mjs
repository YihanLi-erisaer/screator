import { copyFileSync, cpSync, mkdirSync, existsSync, chmodSync, readFileSync, readdirSync, rmSync, symlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

if (process.platform !== "darwin" || process.arch !== "arm64")
  throw new Error("Run the macOS package build with native arm64 Node.js on Apple Silicon.");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const python = process.env.YT2BILI_BUILD_PYTHON || path.join(root, ".desktop-venv/bin/python");
if (!existsSync(python))
  throw new Error("Create .desktop-venv with native arm64 Python 3.11+ and install requirements-desktop.txt.");
function run(executable, args, cwd = root) {
  const result = spawnSync(executable, args, { cwd, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${executable} exited with status ${result.status ?? "unknown"}`);
}
function output(executable, args) {
  const result = spawnSync(executable, args, { cwd: root, encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error(result.stderr || String(result.error));
  return result.stdout.trim();
}
if (output(python, ["-c", "import platform; print(platform.machine())"]) !== "arm64")
  throw new Error("The packaging Python interpreter must be native arm64.");
const icon = path.join(root, "desktop/src-tauri/icons/icon.png");
run("/usr/bin/sips", ["-s", "format", "icns", icon, "--out", path.join(root, "desktop/src-tauri/icons/icon.icns")]);
const tools = path.join(root, "packaging/staging/macos-bin");
mkdirSync(tools, { recursive: true });
function checkNativeExecutable(name, source) {
  const description = output("/usr/bin/file", ["-L", source]);
  if (!description.includes("arm64")) throw new Error(`${name} must contain native arm64 code: ${description}`);
  const linked = output("/usr/bin/otool", ["-L", source]).split("\n").slice(1)
    .map((line) => line.trim().split(/\s+/)[0]).filter(Boolean);
  const external = linked.filter((dependency) => !dependency.startsWith("/usr/lib/")
    && !dependency.startsWith("/System/Library/"));
  if (external.length) throw new Error(`${name} has libraries outside macOS that the app will not bundle: ${external.join(", ")}`);
}
for (const name of ["ffmpeg", "ffprobe", "biliup"]) {
  const source = path.join(root, "bin", name);
  if (!existsSync(source)) throw new Error(`Missing arm64 tool: ${source}`);
  checkNativeExecutable(name, source);
  if (name === "ffmpeg" && !output(source, ["-hide_banner", "-hwaccels"]).split(/\r?\n/).includes("videotoolbox"))
    throw new Error("Bundled FFmpeg must support VideoToolbox hardware decoding.");
  copyFileSync(source, path.join(tools, name));
  chmodSync(path.join(tools, name), 0o755);
}
checkNativeExecutable("node", process.execPath);
copyFileSync(process.execPath, path.join(tools, "node"));
chmodSync(path.join(tools, "node"), 0o755);
run(python, ["scripts/build_worker.py"]);
const frozen = path.join(root, "packaging/staging/screator-worker/screator-worker");
if (!output("/usr/bin/file", ["-L", frozen]).includes("arm64"))
  throw new Error("Frozen Python worker is not arm64.");
run(python, ["scripts/smoke_worker.py", "--frozen", frozen]);
run(process.execPath, ["desktop/scripts/tauri.mjs", "build", "--no-bundle", "--config", "src-tauri/tauri.macos-release.conf.json", "--", "--locked"]);
run(process.execPath, ["desktop/scripts/tauri.mjs", "bundle", "--bundles", "app", "--config", "src-tauri/tauri.macos-release.conf.json"]);
const built = path.join(root, "desktop/src-tauri/target/release/bundle");
const app = path.join(built, "macos/Screator.app");
if (!existsSync(app)) throw new Error(`Missing macOS application: ${app}`);
const destination = path.join(root, "dist/macos");
mkdirSync(destination, { recursive: true });
rmSync(path.join(destination, "Screator.app"), { recursive: true, force: true });
const version = JSON.parse(readFileSync(path.join(root, "desktop/package.json"), "utf8")).version;
const dmg = path.join(destination, `Screator_${version}_aarch64.dmg`);
const dmgStage = path.join(root, "packaging/staging/macos-dmg");
rmSync(dmgStage, { recursive: true, force: true });
mkdirSync(dmgStage, { recursive: true });
try {
  const packagedApp = path.join(dmgStage, "Screator.app");
  cpSync(app, packagedApp, { recursive: true, force: true });
  run("/usr/bin/codesign", ["--force", "--deep", "--sign", "-", packagedApp]);
  run("/usr/bin/codesign", ["--verify", "--deep", "--strict", packagedApp]);
  run(python, ["scripts/smoke_native.py", "--release", path.join(packagedApp, "Contents/MacOS/screator-desktop")]);
  symlinkSync("/Applications", path.join(dmgStage, "Applications"));
  run("/usr/bin/hdiutil", ["create", "-ov", "-format", "UDZO", "-volname", "Screator", "-srcfolder", dmgStage, dmg]);
  run("/usr/bin/hdiutil", ["verify", dmg]);
} finally {
  rmSync(dmgStage, { recursive: true, force: true });
  rmSync(app, { recursive: true, force: true });
  for (const name of readdirSync(path.dirname(app))) {
    if (/^rw\.\d+\.Screator_.*\.dmg$/.test(name))
      rmSync(path.join(path.dirname(app), name), { force: true });
  }
}
console.log(`macOS arm64 DMG: ${dmg}`);
