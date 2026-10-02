# macOS Apple Silicon 安装与打包

支持 macOS 14+、M 系列芯片。桌面窗口、Python 后台、FFmpeg、biliup 和 Node.js 均应运行 arm64 代码。视频完整校验优先使用 FFmpeg 的 VideoToolbox 硬件解码；不支持的编码、硬件失败或解码出错时，从头用 CPU 复核。VideoToolbox 使用 Apple 的媒体硬件；本地大模型翻译由 Ollama 的 Metal 后端使用 GPU。试译后界面会显示 Ollama 报告的实际推理设备。

## 开发运行

安装原生 arm64 Python 3.11+、Node.js 22+、Rust stable、FFmpeg（含 `ffprobe`）、biliupR。检查 `python3`、`node`、`cargo`、`ffmpeg`、`ffprobe` 和 `biliup` 可从终端执行。biliupR 的 Apple Silicon 包名包含 `aarch64-macos.tar.xz`；也可运行 `python -m yt2bili setup` 下载到项目 `bin/`。

```sh
python3 -m venv .desktop-venv
.desktop-venv/bin/python -m pip install -r requirements-desktop.txt
cd desktop
npm ci
npm run desktop
```

桌面数据位于 `~/Library/Application Support/StarDazz/yt2bili/`。首次在设置中安装本地翻译组件：应用会下载并校验固定版本的 Ollama macOS 运行时及 Qwen3.5 4B 模型，单独保存在数据目录的 `translation/` 下。无需先启动系统 Ollama。若已安装 Ollama，也可切换为“连接已有本机 Ollama”，默认地址为 `http://127.0.0.1:11434`。在“本地试译”结果中核对 `Apple GPU (Metal)`；显示 `CPU` 说明该次模型没有用 GPU，应检查系统版本、模型内存和 Ollama 的硬件支持。

完整校验默认选择 `auto`。需只用 CPU 时，在设置里选“仅 CPU”，或对 CLI 设置 `YT2BILI_HWACCEL=cpu`。AV1 的 VideoToolbox 支持取决于芯片代际；不支持时会自动 CPU 复核。日志和进度会显示 `VideoToolbox`、`GPU` 或 `CPU`。

## 生成 arm64 应用与 DMG

将原生 arm64 的 `ffmpeg`、`ffprobe`、`biliup` 放在项目根目录 `bin/`。发布脚本会检查这三个文件包含 arm64 代码，且不依赖包外的 Homebrew 动态库；FFmpeg 应使用可独立分发、启用 VideoToolbox 的构建。Node.js 从当前 arm64 Node 复制。确保已按上文安装 Python 依赖和前端依赖，然后执行：

```sh
cd desktop
npm run desktop:package:mac
```

脚本依次冻结 arm64 Python 后台、运行离线后台测试、构建 Tauri 原生应用、做本机临时签名并验证原生窗口启动，最后用 macOS 的 `hdiutil` 生成和校验 DMG。输出在 `dist/macos/`。打开 DMG 后将 `yt2bili.app` 拖入 `Applications` 即可安装。也可设置 `YT2BILI_BUILD_PYTHON` 指向另一套已装依赖的 arm64 Python 环境。

仓库打包时使用临时签名，未做 Apple Developer ID 签名和公证；首次打开时 macOS 可能要求在“隐私与安全性”中明确允许。若要向其他用户公开分发且正常通过 Gatekeeper，需用自己的 Apple Developer ID 重新签名并公证。发布前还须核对随包 FFmpeg 和其它二进制的许可证。此仓库没有附带或自动下载这些发布用二进制。

## 验证范围

可运行 `.desktop-venv/bin/python -m unittest discover -s tests -v`、`npm run build` 和 `npm run test:launcher`。GPU 验收需用真实 M 系列设备、含 H.264/HEVC/AV1 的授权测试素材以及实际模型试译；模拟测试只覆盖设备选择、失败回退、归档安全与流程状态。
