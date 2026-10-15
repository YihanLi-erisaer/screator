import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const ffmpeg = process.argv[2] || path.join(root, "bin/ffmpeg");
try {
  for (const codec of ["h264", "hevc", "av1"]) {
    const decoder = execFileSync(ffmpeg, ["-hide_banner", "-h", `decoder=${codec}`],
      { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] });
    if (!/^\s*Supported hardware devices:.*\bvideotoolbox\b/m.test(decoder))
      throw new Error(`Bundled FFmpeg's native ${codec} decoder must support VideoToolbox. A generic -hwaccels listing is insufficient; replace bin/ffmpeg and bin/ffprobe with a compatible standalone arm64 build. See docs/macOS安装与打包.md.`);
  }
  console.log("FFmpeg VideoToolbox decoder support verified: H.264, HEVC, AV1.");
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
