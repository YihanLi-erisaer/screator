# screator

项目名称统一为小写 `screator`。项目仓库为 [YihanLi-erisaer/screator](https://github.com/YihanLi-erisaer/screator)。

Python 包和命令均为 `screator`；桌面应用、安装包和配置项也采用小写名称。已有安装的数据目录及系统凭据会继续使用，避免改名后丢失任务和设置。CLI 支持 `python -m screator`，安装项目后也可使用 `screator` 命令。

把**你有权转载**的 YouTube 视频下载下来，默认用本地开源大模型把标题和简介译成中文（也可优先使用 DeepL），再投稿到 B 站。

支持一次传入多条链接：下载和封面处理可以并行，B 站上传会自动排队（同一账号不并行投稿）。创作声明为「内容无需标注」，简介末尾会带上原标题、原作者和原链接。

## 可视化桌面版

现已提供与 StarDazz 官网风格一致的桌面应用，支持任务管理、素材预览与编辑、扫码登录、设置、日志和投稿操作。Windows 安装包和免安装版输出在 `dist/windows/`，使用与构建步骤见 [Windows 安装与打包](docs/Windows安装与打包.md)。

双击根目录 `启动桌面版.cmd` 运行 `dist/windows/screator/screator.exe`；首次运行前请先用 `打包Windows.cmd` 构建。需要热更新开发时使用 `启动开发版.cmd`，或执行 `cd desktop` 后运行 `npm run desktop`。首次使用请从投稿中心的“开始配置”进入。

macOS Apple Silicon 可按 [macOS 安装与打包](docs/macOS安装与打包.md) 在 M 系列设备上原生运行和构建 DMG。开发环境与使用方法见 [桌面版说明](desktop/README.md)，实现与测试记录见 [第一期开发与验收记录](第一期开发与验收记录.md)。以下仍是原有 CLI 用法。

## 素材管理与本地视频投稿

- 「素材管理」位于投稿中心下方。素材库存放于应用数据目录的 `materials/素材库名称/`，可创建文件夹，搜索、按类型筛选、重命名、删除素材，并使用系统默认应用预览。删除素材不删除下载记录。
- 素材管理 → 新建下载任务：在“新建下载素材任务”窗口选择链接、视频/图片/音频类型及素材库，无需绑定投稿账号或配置翻译。每次下载有独立任务记录与文件名，同一个链接可以重复下载；失败可继续、排队可取消。视频/音频使用 [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)，图片帖子使用 [gallery-dl](https://github.com/mikf/gallery-dl/blob/master/docs/supportedsites.md)，也支持媒体文件直链。一次一个作品，图片最多 100 张，音频模式可提取视频音轨。平台登录要求、地区限制及网站改版可能影响下载，接入不代表所有链接均可下载。
- 投稿中心 → 新建投稿任务：在“新建投稿任务”窗口选择视频上传或图文上传；视频可使用 YouTube 链接或本地视频文件，并填写标题与简介。本地视频先复制到独立任务目录，再执行真实媒体校验、封面抽帧及现有投稿队列，跳过下载和翻译；投稿后保留源文件和任务目录。两秒等短视频按时长选择封面抽帧位置。
- 数据库升级至 v6，旧库升级前自动备份。图片组件包含于依赖和桌面 worker 打包配置中。使用本地视频同步抖音时，已部署的 `douyin-broker` 也需更新本仓库版本，以接受本地任务的源 ID。
- 本地验证涵盖 HTTP 下载、图片适配层、重复下载、文件管理与路径边界、迁移备份、取消/重试、真实 FFmpeg 校验和封面、模拟投稿及 UI。真实平台发布、所有站点实链与重新打包后的安装版仍需单独验证。

## 小红书图文同步（实验性网页接入，待真实账号联调）

图文上传可勾选「同步上传小红书」，业务流程与 AcFun 视频同步相同：共用图片和正文，各平台独立排队、保存结果；预览后一起确认，自动模式分别发布。未勾选时沿用 Bilibili 图文动态流程。

1. 本机安装 Google Chrome 或 Microsoft Edge。在「账号与连接 → 小红书图文同步」点击「登录小红书」，在打开的独立浏览器窗口完成登录与网页安全验证。一次绑定一个小红书账号；登录态保存在系统凭据库。
2. 在「新建投稿任务 → 图文上传」选择 Bilibili 账号，勾选同步小红书并填写独立标题。当前同步支持 1～9 张 JPG/PNG、每张不超过 10 MB；小红书标题最多 20 字，共用正文最多 1000 字（按网页 UTF-16 字数计数）。单独发 B 站仍支持 GIF 和 2000 字正文。
3. 图片准备完成后，可在任务详情编辑共用正文和小红书独立标题，再确认投稿。发布时会打开浏览器，通过创作中心网页上传图片并提交；平台要求验证时在网页操作。登录失效可重新登录并恢复小红书队列。
4. 某个平台失败时只继续对应目标，已成功目标不会重发。点击发布后超时、网页关闭或缺少笔记 ID 会进入待核对；先在对应账号创作中心核实，再登记 24 位笔记 ID，或明确确认未提交后继续。图片素材保留，历史记录支持导出、导入和删除。

这不是小红书官方发布 API。网页变化可能需要更新适配层；本地模拟、浏览器夹具和界面测试不代表真实平台验收。数据库升级至 v11，升级前自动备份。开发环境需更新 `requirements-desktop.txt`；安装版需重新打包以包含 Playwright 组件，浏览器由本机 Chrome/Edge 提供，无需下载额外 Chromium。

## YouTube 同步投稿（官方 API，待真实账号联调）

YouTube 与 AcFun 使用相同的同步投稿流程：绑定一个频道，共用视频、封面、简介及 Bilibili 标签，使用独立串行上传队列。支持 YouTube 链接或本地视频任务；当前仍需选择 Bilibili 账号，再勾选「同步上传 YouTube」。图文任务不支持同步到 YouTube。

1. 在 [Google Cloud](https://console.cloud.google.com/) 创建项目并启用 **YouTube Data API v3**，配置 OAuth 同意屏幕；测试模式下将登录账号添加为测试用户。
2. 创建类型为 **桌面应用（Desktop app）** 的 OAuth 客户端并下载 JSON。桌面「账号与连接 → YouTube」选择文件、导入配置，然后点击「在浏览器授权 YouTube」。选择目标频道并允许上传及频道读取权限；应用通过本机回调自动完成绑定，无需部署授权服务器。
3. 「设置 → 投稿默认值」选择 YouTube 分类、可见性及是否面向儿童。默认分类为人物与博客（22），默认私密、非儿童内容；创建任务时保存快照，后续更改默认值不影响旧任务。
4. 新建视频投稿任务，勾选「同步上传 YouTube」。预览模式下可单独编辑 YouTube 标题（最多 100 字）、分类、可见性及儿童内容设置；简介、标签和封面共用本任务素材。简介最多 5000 UTF-8 字节，不能包含尖括号；提交前会校验。自动模式使用创建时的默认参数。
5. 投稿结果会保存 YouTube 视频 ID，并可打开视频或 YouTube Studio。失败时仅继续未完成的平台；结果待核对时禁止重发，需在 Studio 核对后登记视频 ID，或明确确认未提交。

OAuth 客户端配置、刷新凭据和断点上传会话均保存在系统凭据库，不写入普通设置、任务快照或历史导出。频道身份不能随旧任务变化；换频道前须完成或放弃旧目标并归档。上传中禁止退出登录。部分上传可通过原会话续传；最终上传响应丢失时会先查询原会话，仍不确定则进入待核对。视频 ID 取得后即保存成功结果；自定义封面失败会保留素材及提示，不能通过重投视频补设封面。上传成功不代表 YouTube 已完成处理或公开视频已可播放。

CLI 可复用同一数据目录中桌面已授权的频道，使用 `run URL --account ACCOUNT_ID --sync-youtube`；按平台确认、继续或核对仍使用 `publication` 子命令。GUI 与 CLI 不能同时使用同一数据目录。

接口参考：[桌面 OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[视频上传](https://developers.google.com/youtube/v3/docs/videos/insert)、[断点续传](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)。本地模拟和 UI 测试已覆盖同步投稿、续传、未知结果保护及迁移；真实账号授权、API 配额、频道封面权限和公开发布仍需联调。

## X（Twitter）、Facebook Page 与 TikTok 视频同步投稿

三个平台均沿用 AcFun 的单账号同步目标流程：在「账号与连接」绑定账号，新建视频任务时勾选平台，共用已下载或复制的本地视频，分别排队、预览、提交和保存结果。每个平台最多绑定一个有效账号，失败只继续该目标；已成功平台不重复提交。当前仍需选择 Bilibili 主投稿账号，图文任务不包含这些视频同步目标。

这是官方 API 接入，需要你自己的开发者应用取得用户授权凭据，不能直接导入网站 Cookie。凭据通过桌面密码输入框绑定并存入系统凭据库；应用在线验证账号身份，任务及历史导出不包含访问凭据、私密上传地址。凭据过期时为同一账号重新绑定，未完成目标归档前须完成、核对或放弃。当前提供凭据绑定入口，尚未实现这些平台的一键 OAuth 浏览器登录和自动刷新。

- **X（Twitter）**：需要 OAuth 用户 Access Token，包含 `users.read`、`tweet.read`、`tweet.write`、`media.write` 权限，并具有对应的 API 访问额度。支持预览及自动模式。视频分片上传、完成处理后才创建帖子；文案可独立编辑，长度及账号权限还会由平台校验。创建帖子响应不明确时必须人工核对该账号主页的帖子 ID，禁止盲目重发。
- **Facebook 公共主页（Page）**：在连接面板填写 Page ID、Graph API 版本（默认 `v25.0`，可按开发者应用配置调整）和 Page Access Token。需要相应 Page 发布权限，通常包含 `pages_manage_posts`、`pages_read_engagement`；个人账号 Token 不能替代 Page Token。支持预览及自动模式，独立标题、共用简介；执行开始会话、视频分片传输、完成发布三个阶段，保存 Facebook 视频 ID。完成发布响应丢失时可查询原视频的 `published` 状态，或人工核对后登记结果。上传接口接受不代表视频处理完成。
- **TikTok**：需要 `user.info.basic` 与 `video.publish` 用户授权及 Content Posting API 权限。**仅支持预览确认，不能使用全自动投稿**。预览时读取最新创作者权限，手动选择可见性（无默认值），评论、合拍、拼接默认关闭，并按账号权限禁用不可用选项；同时确认原创内容、推广内容声明、AI 内容标记和对应的音乐使用/品牌内容政策。可通过系统播放器预览素材。更改文案或发布选项后须重新确认声明并保存，再确认投稿。封面使用视频内的帧位置，不使用单独的封面图片。上传后查询原 `publish_id` 的处理状态，`PUBLISH_COMPLETE` 才记为成功；私密作品没有公开视频 ID 时保存发布标识。处理状态无法确认则保留素材，使用「查询原发布状态」继续核对，不重新上传。

TikTok 官方将未经审核的客户端限制为私密账号、仅自己可见等测试范围；公开发布需审核。其接入要求强调原创创作者及面向广泛用户的产品，并明确排除任意跨平台复制和仅服务自己/团队账号的工具。本项目已有搬运流程，因此不能保证 TikTok Direct Post 审核通过；部署和使用前应确认你的开发者应用及业务场景符合要求。现有权限不足会阻止投稿，不会通过网页接口绕过限制。[TikTok 接入要求](https://developers.tiktok.com/docs/en/content-sharing-guidelines)

三个适配层会在需要时生成各自的 H.264/AAC 兼容文件；原视频和其他平台使用的文件保留。失败或待核对目标会阻止共享素材清理，所有目标完成后才沿用原清理规则。TikTok 的分片及最后一片尾部字节合并遵循官方上传规范。

CLI 增加 `--sync-twitter`、`--sync-facebook`、`--sync-tiktok`，复用同一数据目录中桌面已绑定账号；TikTok 不允许与 `--auto` 一起使用。各平台可通过现有 `publication` 命令单独确认、继续、取消或核对。

验证覆盖身份绑定、预览/自动模式边界、三种上传流程、未知结果保护、TikTok 权限/时长/分片及凭据隔离；桌面预览也覆盖同步选项、平台文案编辑与 TikTok 确认流程。**真实开发者应用授权、真实上传和应用审核尚未联调，安装包需重新构建才能包含新功能。** 接口参考：[X 上传接口](https://docs.x.com/x-api/media/initialize-media-upload)、[Meta Page 视频接口 SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/page.py)、[TikTok Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post)。

## 抖音同步投稿（官方接入，待真实平台联调）

新建任务可选择同步到一个已绑定的抖音账号，未登录时禁用。下载、校验、封面与翻译共用，之后分别进入 Bilibili 账号队列和单路抖音队列；预览时一起确认，自动模式分别自动投稿。各平台独立保存结果，失败重试不重复已成功目标。

此功能需要部署 HTTPS 官方授权服务、获得抖音应用发布权限并完成用户授权；不是导入 Cookie 即可使用。部署与使用见 [抖音服务说明](services/douyin-broker/README.md)，本地验证及限制见 [实现验收记录](docs/抖音同步投稿实现与验证.md)。CLI `run --sync-douyin` 复用同一账号配置，`publication` 子命令可按平台继续、确认、取消或核对结果。

## 使用前

请确认视频属于下列情形之一：你拥有版权、已获授权、或源平台明确允许转载。YouTube 用户协议和 B 站社区规范都可能禁止未授权搬运。

## 环境

- Windows 10/11 或 macOS 14+ Apple Silicon
- Python 3.11+
- [FFmpeg](https://ffmpeg.org/download.html)（`ffmpeg` 和 `ffprobe` 都要在 PATH 里）
- 本地翻译组件，或可选的 [DeepL API](https://www.deepl.com/pro-api) 密钥
- 可以正常网页投稿的 B 站账号

## 安装

在项目目录：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

默认本地优先，执行 `python -m screator translation setup` 安装固定版本的 Ollama 和 Qwen3.5 4B，再用 `python -m screator translation test` 试译。首次下载约 4.9 GB；安装会检查空间。

本地推理支持兼容的 Intel 和 AMD GPU。Windows 应用管理模式默认启用 Ollama Vulkan 和核显检测，同时保留 NVIDIA CUDA / AMD ROCm 自动选择；Apple Silicon 继续使用 Metal。桌面“本地推理设备”或 `.env` 中的 `LOCAL_LLM_BACKEND=auto|vulkan|cpu` 可选择自动、Vulkan 或仅 CPU。Vulkan 选项用于 Windows 托管运行时；macOS 请选择自动或 CPU。没有可用 GPU 时，Ollama 可使用 CPU；试译结果根据 `/api/ps` 的显存分配显示 GPU、CPU 或 GPU + CPU，不根据配置猜测显卡厂商。

Linux 和其他使用外部 Ollama 的环境，在 **Ollama 服务进程** 中设置 `OLLAMA_VULKAN=1`、`OLLAMA_IGPU_ENABLE=1` 后重启服务，在本项目中选外部模式及 `LOCAL_LLM_BACKEND=auto`。需安装包含 Vulkan 后端的 Ollama 和兼容显卡驱动；AMD ROCm 支持范围、Linux Vulkan 驱动与设备权限见 [Ollama 硬件文档](https://docs.ollama.com/gpu)。多卡可用 `GGML_VK_VISIBLE_DEVICES` 选择 Vulkan 设备，`OLLAMA_IGPU_ENABLE=0` 禁用核显；自动模式保留这些显式环境设置。外部服务的环境由用户管理，应用不会修改或重启它。仅 CPU 选项通过请求的 `num_gpu=0` 生效。

如需 DeepL，在 `.env` 填入可选的 `DEEPL_AUTH_KEY`，设置 `TRANSLATION_PRIMARY=deepl` 可优先使用它。`TRANSLATION_FALLBACK_ENABLED=false` 关闭自动切换。密钥不要提交到 Git。详细部署、离线导入与验收边界见[本地翻译实现说明](docs/本地翻译实现与验证.md)。

下载投稿工具并检查 FFmpeg：

```powershell
python -m screator setup
```

这会把官方 [biliupR](https://github.com/biliup/biliup/releases/latest)（原 biliup-rs）放到 `bin\biliup.exe`。也可以自己下载 `biliupR-*-x86_64-windows.zip`，把 `biliup.exe` 放进 `bin\`。

添加 Bilibili 账号（最多 5 个不同 UID，凭据保存在忽略提交的 secrets 目录）：

```powershell
python -m screator accounts add
python -m screator accounts list
```

## 使用

每次只输入一个 YouTube 视频链接，必须用 `--account` 选择账号。默认只准备素材，完成后在桌面预览编辑并确认投稿；也可用 CLI 确认：

```powershell
python -m screator run "https://www.youtube.com/watch?v=xxxxxxxxxxx" --account ACCOUNT_ID
python -m screator submit TASK_ID
```

明确需要自动投稿时增加 `--auto`。不再支持多 URL、TXT 列表、`--file` 或 `--force`。同视频同账号返回原任务，同视频可分别创建到不同账号。

```powershell
python -m screator run "https://youtu.be/xxxxxxxxxxx" --account ACCOUNT_ID --auto
python -m screator retry TASK_ID
python -m screator repair TASK_ID
python -m screator login --account ACCOUNT_ID
python -m screator renew --account ACCOUNT_ID
python -m screator accounts archive --account ACCOUNT_ID
python -m screator list
```

重试始终回到预览，目标账号不能改变。归档只允许该账号全部任务已提交；清除登录凭据仍占账号名额。重新添加已归档的 UID 会恢复原账号身份。

新素材目录为 `work/<task_id>/`，历史视频 ID 有且仅有一个匹配时才允许作为 CLI 参数。GUI 与 CLI 不能同时执行同一个数据目录；指定同一份库时先退出桌面：

```powershell
python -m screator --data-dir "C:\\path\\to\\profile" list
```

启动会备份旧数据库并升级到 schema v3（兼容旧单账号库及两种 v2 多账号/翻译库）。旧任务不会自动判断历史投稿账号，需在桌面详情中一次性绑定。旧 Cookie 能确定本地 UID 时仅导入为待验证账号，原文件保留。新旧版本不支持混跑；回滚需先另存升级后的数据库，再恢复升级前备份及对应旧版本，不能直接降低 schema 版本号。备份后新增任务不在旧库中，素材目录应保留。

## 默认投稿参数

| 项 | 默认 | 如何改 |
|----|------|--------|
| 分区 | `171`（知识 · 野生技术协会，biliup 默认） | `.env` 里 `BILI_TID` |
| 上传线路 | `tx`（避开 Windows 上证书常失效的 bldsa） | `.env` 里 `BILI_LINE`，如 `bda2` / `qn` |
| 标签 | `转载`（B 站投稿需要至少一个标签） | `.env` 里 `BILI_TAGS` |
| 创作声明 | 内容无需标注（Web 投稿，不勾选自制禁转） | 固定 |
| 分辨率 | 源站最高可用画质（含 4K）。禁止嵌入的视频可能只能下到 1080p | 无上限 |
| 时长 | 不限制 | — |
| 队列并发 | 下载、校验各共享 1 路；每账号上传 1 路，最多 5 路 | 账号队列自动创建 |
| 上传间隔 | 同 UID 上一次上传尝试结束后至少 20 秒再开始下一次 | `.env` 里 `UPLOAD_GAP_SECONDS` |

YouTube 现在要求 JS 运行时才能完整解析。本机有 Node.js 或 Deno 即可（`setup` 会检测）。若出现「Sign in to confirm you’re not a bot」，先完全退出 Edge/Chrome，再导出 cookies：

```powershell
python -m screator youtube-cookies
```

之后会自动使用 `secrets\youtube_cookies.txt`。也可以在 `.env` 里写 `YOUTUBE_COOKIES_FROM_BROWSER=edge`（每次下载前都要退出浏览器）。

## 流程

1. 解析链接元数据  
2. 下载最高画质并完整解码检查音视频轨道；完整 MP4 直接上传原文件（包括 AV1/VP9，不重新编码、不降低分辨率）。只有非 MP4 文件才转换为 H.264/yuv420p + AAC 的 MP4
3. 下载封面，裁成 1280×720 JPEG；失败则从视频抽帧  
4. 按用户选择的优先级翻译标题和简介（默认本地，失败可切换 DeepL；已是中文则跳过），标题截到 80 字
5. 调用 `biliup upload` 提交稿件（创作声明：内容无需标注）  

`--dry-run` 在第 5 步之前停下并保留文件。Cookie 过期时使用 `login --account ACCOUNT_ID`。上传成功并获得 BV 号后，自动删除当前任务的 `work\<video_id>\` 目录（含源视频、封面、临时文件及隔离的旧文件），保留数据库中的任务记录和 BV 号。上传失败或未解析到 BV 号时保留文件；清理失败也不会把已提交的任务改成上传失败。投稿成功不代表平台转码或审核成功，删除后若平台处理失败，需要重新下载。

平台转码失败时，可生成完整、兼容的替换文件，命令不会重复投稿或修改原 BV 号：

```powershell
python -m screator repair TASK_ID
```

修复会先尝试复用本任务完整素材，缺失或损坏时重新准备。成功后使用日志显示的路径在创作中心替换原稿件视频；不上传、不修改 BV、不触发成功清理。

完整校验在 Windows 上默认先尝试 D3D11VA（Intel / AMD / NVIDIA），失败后尝试适用的 CUDA 解码；Linux 先尝试适用的 CUDA，再尝试各 DRM render 节点的 VAAPI（Intel / AMD）；macOS 对 H.264/HEVC/AV1 尝试 VideoToolbox。D3D11VA/VAAPI 支持普通 8/10-bit 4:2:0 的 H.264/HEVC/AV1/VP9，实际能力取决于显卡、驱动和 FFmpeg 构建。每次硬件尝试必须输出硬件帧；失败会从头尝试下一方式，最后用 CPU 复核。其它格式、HDR 和音频使用 CPU（macOS VideoToolbox 的既有 HDR 路径保留）。CPU 处理 AV1 时建议使用包含 `libdav1d` 的 FFmpeg full 构建。

可在桌面设置选择校验后端，或设置 `SCREATOR_HWACCEL=auto|cpu|cuda|d3d11va|vaapi|videotoolbox`。显式选择只尝试该硬件后端，失败仍从头用 CPU 复核。多卡可设置 `SCREATOR_HWACCEL_DEVICE`：Windows 使用 D3D11 适配器索引（如 `1`），Linux VAAPI 使用 render 节点（如 `/dev/dri/renderD129`），显式 CUDA 模式使用 CUDA 设备索引。设备设置参与校验缓存指纹；修改后重新解码。后端和设备选项参考 [FFmpeg 文档](https://ffmpeg.org/ffmpeg.html#Advanced-Video-options)。

完整校验成功后，会在视频旁写入 `<文件名>.validation.json` 缓存，程序重启后仍可复用。每次先检查轨道信息并计算**整文件 SHA-256**，只有内容、文件状态、预期时长、解码模式、校验规则版本及 FFmpeg/ffprobe 工具状态均匹配才跳过重复解码；仍需顺序读取整个文件，但无需再次逐帧解码。文件中间内容变化（即使大小和修改时间不变）也会使缓存失效。失败或中断的校验不写成功缓存；缓存损坏时重新校验，缓存写入失败不影响已通过的结果。上传后清理任务目录时缓存一并删除。

需要强制重新完整校验时，设置 `$env:SCREATOR_VALIDATION_CACHE='0'`；恢复默认缓存用 `Remove-Item Env:SCREATOR_VALIDATION_CACHE`。以上设置也可写入 `.env`。这只影响校验方式，完整 MP4 仍直接上传，不会重新转码。

`.part` 文件只由 yt-dlp 续传、完成后改名，程序不再根据文件头的时长把它提前当作成品。分片下载失败、解码报错、音视频长度不匹配会阻止上传。FFmpeg 可安装到 PATH，也可将 `ffmpeg.exe` 和 `ffprobe.exe` 放在项目 `bin/` 中。

任务使用两个共享队列及 0～5 个账号上传队列：

- 下载队列负责解析、下载、合并，完成后即处理下一条。
- 校验队列保留完整解码、GPU/CPU 复核及缓存逻辑。校验失败隔离旧文件并回到下载队尾，最多五轮。
- 每个账号单独准备封面、翻译和上传。等待登录、冷却或限流不会占用其他账号；等待期间该账号后续预览任务仍可准备。

账号间可同时上传，同 UID 即使使用不同 profile 或 Cookie 路径也共享操作系统锁与冷却时间。未知投稿必须先核对创作中心，不会自动重发；异常退出后其他未完成任务也需手动继续。正常退出会取消未投稿任务并等待所有在途投稿结束。

`accounts.resume_uploads`（桌面“恢复上传”）用于人工核对后的限流或异常占用恢复。残留上传进程仍存在时拒绝恢复；恢复队列不会解除任务自身的“待核对”保护。

完整设计见 [PRD](docs/多账号上传与单链接任务PRD.md) 和 [技术实现方案](docs/多账号上传与单链接任务技术实现方案.md)。真实平台五账号验收与自动化模拟验证分开记录。
