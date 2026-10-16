# macOS Apple Silicon 安装与打包

支持 macOS 14+、M 系列芯片。桌面窗口、Python 后台、FFmpeg、biliup 和 Node.js 均应运行 arm64 代码。视频完整校验优先使用 FFmpeg 的 VideoToolbox 硬件解码；不支持的编码、硬件失败或解码出错时，从头用 CPU 复核。VideoToolbox 使用 Apple 的媒体硬件；本地大模型翻译由 Ollama 的 Metal 后端使用 GPU。试译后界面会显示 Ollama 报告的实际推理设备。

## 开发运行

安装原生 arm64 Python 3.11+、Node.js 22+、Rust stable、FFmpeg（含 `ffprobe`）、biliupR。检查 `python3`、`node`、`cargo`、`ffmpeg`、`ffprobe` 和 `biliup` 可从终端执行。biliupR 的 Apple Silicon 包名包含 `aarch64-macos.tar.xz`；也可运行 `python -m screator setup` 下载到项目 `bin/`。

```sh
python3 -m venv .desktop-venv
.desktop-venv/bin/python -m pip install -r requirements-desktop.txt
cd desktop
npm ci
npm run desktop
```

桌面数据位于 `~/Library/Application Support/StarDazz/screator/`。首次在设置中安装本地翻译组件：应用会下载并校验固定版本的 Ollama macOS 运行时及 Qwen3.5 4B 模型，单独保存在数据目录的 `translation/` 下。无需先启动系统 Ollama。若已安装 Ollama，也可切换为“连接已有本机 Ollama”，默认地址为 `http://127.0.0.1:11434`。在“本地试译”结果中核对 `Apple GPU (Metal)`；显示 `CPU` 说明该次模型没有用 GPU，应检查系统版本、模型内存和 Ollama 的硬件支持。

完整校验默认选择 `auto`。H.264、HEVC 和 AV1 视频会优先尝试 VideoToolbox，并显式选择对应的 FFmpeg 原生解码器（`-c:v h264|hevc|av1`），避免默认选中无法调用 VideoToolbox 的 libaom/libdav1d 软件解码器。校验要求 FFmpeg 实际输出硬件帧；硬件不可用或无法解码时，从头用 CPU 复核。音频仍由 CPU 校验。需只用 CPU 时，在设置里选“仅 CPU”，或对 CLI 设置 `SCREATOR_HWACCEL=cpu`。AV1 硬解从 M3 系列开始支持，M1/M2 使用 CPU；FFmpeg 构建也必须包含 AV1 VideoToolbox 支持。日志和进度会显示 `VideoToolbox` 或 `CPU`。

## 生成 arm64 应用与 DMG

项目根目录 `bin/` 被 Git 忽略，重新克隆或清理工作目录后需要单独准备发布工具。将原生 arm64 的 `ffmpeg`、`ffprobe`、`biliup` 放入该目录。发布脚本会检查这三个文件包含 arm64 代码，且不依赖包外的 Homebrew 动态库；还会逐一检查 FFmpeg 原生 H.264、HEVC、AV1 解码器的 `Supported hardware devices` 包含 `videotoolbox`，仅有 `-hwaccels` 列出该名称不算通过。可以单独执行 `node desktop/scripts/check-macos-media.mjs` 检查。

本次使用 [OSXExperts 的独立 arm64 FFmpeg 9.0 构建](https://www.osxexperts.net/)，下载 [ffmpeg9arm.zip](https://www.osxexperts.net/ffmpeg9arm.zip) 和 [ffprobe9arm.zip](https://www.osxexperts.net/ffprobe9arm.zip)，解压到临时目录。核对解压后可执行文件的 SHA-256，再同时替换 `bin/ffmpeg` 和 `bin/ffprobe`，并保留可执行权限：

```text
ffmpeg   591260c945d0eef150e3bf82b0ef988bd36a9cecc18ff05d6679617159f0a95e
ffprobe  e11c17e8200b3ee4c4c186d245e2b4053f01d56957336c1817fca0b997469106
```

以上是本次验证版本的文件哈希；供应方若更新同名下载，需重新核对发布页面和实际解码能力，不能跳过校验。旧的 7.1 随包构建不含 AV1 VideoToolbox 支持，不能继续用于此发布。替换项目 `bin/` 后需重新打包、安装，已经运行的旧应用不会自动更新。

随包 Node.js 按 `SCREATOR_BUILD_NODE` 指定路径、项目 `bin/node`、执行打包脚本的 Node.js 的顺序选择。若使用 Homebrew Node.js，请从 [Node.js 官方发行目录](https://nodejs.org/dist/) 下载 Node.js 22+ 的 `darwin-arm64.tar.gz`，把其中的 `bin/node` 复制到项目 `bin/node`；Homebrew 的 Node.js 可能依赖本机动态库，不能直接放进安装包。

若本机已安装以前构建的应用，也可以从其 `Contents/Resources/bin/` 复制 `ffmpeg`、`ffprobe`、`biliup`、`node` 到项目 `bin/`，脚本仍会验证架构、动态库依赖和 VideoToolbox。确保四个文件有可执行权限。确认已按上文安装 Python 依赖和前端依赖，然后执行：

```sh
cd desktop
npm run desktop:package:mac
```

脚本依次冻结 arm64 Python 后台、运行离线后台测试、构建 Tauri 原生应用、做本机临时签名并验证原生窗口启动，最后用 macOS 的 `hdiutil` 生成和校验 DMG。`dist/macos/` 只保留 DMG；构建结束会清理 Tauri 的 `.app` 和旧的临时 `rw.*.dmg`，也不会复制应用到 `/Applications`。也可设置 `SCREATOR_BUILD_PYTHON` 指向另一套已装依赖的 arm64 Python 环境。

打开 DMG 后，双击其中的 `screator.app` 会先显示安装提示。程序检查 `/Applications/screator.app` 和 `~/Applications/screator.app`；已有安装时显示两个版本及安装位置，可选择“打开已安装版本”或“替换并打开”。首次安装默认写入 `/Applications`。取消操作不会从 DMG 启动业务后台。也可以手动拖入 `Applications` 安装或替换；单独打开 DMG 只挂载磁盘镜像，不会执行安装代码。

替换前需退出正在运行的 screator。安装程序先复制并校验新应用签名，再替换旧应用；复制或校验失败会保留旧版本，提交替换或请求启动失败时会尝试恢复旧版本。账号、设置、任务及本地翻译组件保存在独立用户数据目录，不会随应用替换。目标文件夹不可写时会提示检查权限或手动安装，不会自动请求管理员权限。安装检查在单实例插件和 Python 后台之前执行，并识别 Gatekeeper 的 AppTranslocation 路径；正常从 Applications 启动及打包冒烟测试均不会弹出安装提示。

安装回归测试（macOS）：`cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml macos_install`。测试在临时目录复制、签名和校验真实的小型应用包，覆盖首次安装、版本替换、取消、打开旧版、运行中阻止替换、签名损坏、启动失败恢复、用户目录安装识别和并发安装锁，不改动本机已安装应用。

仓库打包时使用临时签名，未做 Apple Developer ID 签名和公证；首次打开时 macOS 可能要求在“隐私与安全性”中明确允许。若要向其他用户公开分发且正常通过 Gatekeeper，需用自己的 Apple Developer ID 重新签名并公证。发布前还须核对随包 FFmpeg 和其它二进制的许可证。此仓库没有附带或自动下载这些发布用二进制。

## 卸载

先退出 screator，再打开新版本 DMG，双击其中的“卸载 screator.command”。卸载工具显示 `/Applications/screator.app` 和 `~/Applications/screator.app` 中找到的应用，提供“卸载并保留数据”（默认）、“卸载并删除数据”和“取消”。直接将应用拖入废纸篓仍会保留数据，macOS 不会在拖入废纸篓时执行卸载代码。

保留数据时只将标准位置的应用移到废纸篓。删除数据时还会再次确认，永久清理 `~/Library/Application Support/StarDazz/screator/` 和旧版 `yt2bili/` 两个目录中的模型、运行时、缓存、历史记录、设置、默认素材及登录文件。确认取消不会删除数据或移除应用。自定义数据/素材目录、外部 Ollama 和系统钥匙串不会清理。没有找到应用时也可通过 DMG 工具清理残留数据。

清理前会检查桌面及命令行任务占用；检测到正在运行的任务、目录链接或权限问题会停止。文件删除失败可能已经清理部分数据，应检查并重试。删除后重装需要重新安装模型，从备份导入需要的历史记录。卸载工具不请求管理员权限；应用移到废纸篓失败时会提示检查权限。

卸载回归测试：`.desktop-venv/bin/python -m unittest discover -s tests -p test_uninstall.py -v` 与 `cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml macos_uninstall`。所有清理测试均使用临时目录，不操作真实用户数据。

## 验证范围

可在项目根目录运行 `PATH="$PWD/bin:$PATH" .desktop-venv/bin/python -m unittest discover -s tests -v`，在 `desktop/` 运行 `npm run build` 和 `npm run test:launcher`。完整测试需允许本地 HTTP 端口和进程监测。GPU 验收需用真实 M 系列设备、含 H.264/HEVC/AV1 的授权测试素材以及实际模型试译；模拟测试只覆盖设备选择、失败回退、归档安全与流程状态。

AV1 全片硬解验收（M3 或更新且支持 AV1 的机器）可执行：

```sh
.desktop-venv/bin/python scripts/verify_macos_av1.py /path/to/av1.mp4 --report build/av1-validation/report.json
```

该脚本使用实际校验函数，禁用校验缓存，要求视频只使用 VideoToolbox；任何视频 CPU 回退都会使验收失败。音频按正常流程使用 CPU。脚本比较校验前后的整文件 SHA-256，确认源视频未被改写，并记录各轨道时长、耗时和 FFmpeg 指纹。视频读取期间请勿移动或清理文件。在限制硬件访问的沙箱内可能无法初始化 VideoToolbox，需在可访问本机媒体硬件的终端运行。

2026-10-15 实测：Apple M3，1920×1080、59.94 fps、AV1/yuv420p + Opus 的 1496.701 秒 MP4。禁用缓存后，视频全轨使用 VideoToolbox 解码 1496.662 秒内容，耗时 194.761 秒；音频由 CPU 校验，耗时 2.720 秒；整个校验函数耗时 197.660 秒。视频未回退 CPU，校验前后整文件 SHA-256 一致。原始报告与日志位于本机 `build/av1-validation/hardware-report.json`、`hardware.log`（被 Git 忽略）。

同次验证完整测试共 444 项：436 项通过，8 项跳过（2 项 NVIDIA、1 项 Windows、5 项需显式启用浏览器的测试）。打包能力检查拒绝旧的 7.1 构建、接受新的 9.0 构建；冻结后台离线测试、前端构建、原生 WebView/IPC、应用签名及 DMG 校验均通过。生成 `dist/macos/screator_1.0.0-alpha_aarch64.dmg`，未替换正在使用的已安装应用。
