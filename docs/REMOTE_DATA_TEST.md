# 在另一台电脑执行采集测试

版本：handoff v1 / 2026-09-16。仓库：[jayson2hu/self-deepsearch-collector](https://github.com/jayson2hu/self-deepsearch-collector)。本手册对应独立测试仓库，不要求启动原 self-deepsearch 的正式网站或数据库。

## 1. 当前可做与尚未实现

| 测试 | 当前状态 | 产出 |
| --- | --- | --- |
| Python 合成管线 | 可运行 | JSONL、36 个实体、重复/冲突/缺失报告 |
| 网站登记与任务配置 | 可运行 | 浏览器本地计划及导出 JSON |
| 桌面 / 手机演示回归 | 可运行；需要 Chromium | 浏览器报告与截图 |
| JavDB / Jable 访问与页面取样 | 可在可访问这些网站的电脑按第 5 节人工执行 | 目标 HTTP 状态、robots、少量公开详情 HTML |
| JavDB 真实页面字段解析 | 已接入本地样本解析器 | 固定版本候选 JSONL、字段证据与 parse report |
| JavDB 有界联网验收 | 已接入人工触发模式 | 最多 5 个显式详情 URL、逐项对账、SQLite staging 与错误表 |
| Jable 演员 HTML 解析与人工浏览器采集 | 已接入快照与有界隔离浏览器模式 | 公开演员姓名、来源 ID、列表作品数及普通头像；逐页入库、续采和归档 |
| 已保存主要数据的离线重建 | `npm run data:prepare` | 4,196 位演员、130 张普通头像、SQLite、JSONL/CSV、离线预览及缺项清单 |
| 生产定时调度 / 自动增量 | 尚未启用 | 需完成来源准入和生产执行器；人工命令可用性取决于当次网络与访问响应 |
| 正式审核发布、跨区调度和 PostgreSQL 入库 | 本仓库未提供 | 后续接入原 self-deepsearch 的 Go 服务 |

**不要设置 `COLLECTION_ENABLED=true` 来尝试打开生产抓取。** 原项目仍有 Release A 守卫；本仓库提供 `collect-review`、`capture:jable` 等人工有界命令，不包含生产网络调度。Jable 的命令与范围见[采集手册](./JABLE_ACTOR_ACQUISITION.md)，当前本地数据入口见[项目与主要数据指南](./PROJECT_DATA_GUIDE.md)。`collector probe --source-id javdb` 仍会被 fixture CLI 拒绝，不代表样本解析器或有界验收命令不可用。

## 2. 获取仓库并检查环境

使用有仓库访问权限的 GitHub 账号或 SSH 密钥克隆。以下命令在新的测试电脑上执行：

```bash
git clone https://github.com/jayson2hu/self-deepsearch-collector.git
cd self-deepsearch-collector
git rev-parse HEAD
node --version
npm --version
```

记录 commit，后续反馈都附带这个版本。Node 要求 22+；Python 要求 3.12+。只有浏览器自动回归需要 npm 第三方依赖。

Windows PowerShell：

```powershell
py -3.12 --version
py -3.12 -m venv .venv
```

Linux/macOS：

```bash
python3 --version
python3 -m venv .venv
```

如果 Python 在其他位置，PowerShell 设置 `$env:PYTHON = '实际的python.exe完整路径'`；Linux/macOS 设置 `export PYTHON=/实际路径/python3`。脚本不自动安装或升级 Python。

## 3. 先验证合成链路

以下 npm 命令可在 PowerShell、bash 或 zsh 中执行：

```bash
npm test
npm run collector -- demo --output-dir runtime/fixture
npm run collector -- validate --input runtime/fixture/candidates.jsonl
npm run collector -- probe --source-id fixture --connector-version fixture@v1 --output-dir runtime/fixture-probe
npm run build:collection-demo
npm run preview:collection-demo
```

如已按第 5 节取得 JavDB 详情页，在 `runtime/source-test/javdb-samples.json` 登记本地文件、规范 URL、核验时间和 `javdb-html@2026-09-16.1`，再运行：

```bash
npm run collector -- parse-samples --manifest runtime/source-test/javdb-samples.json --output-dir runtime/source-test/parsed
npm run collector -- validate --input runtime/source-test/parsed/candidates.jsonl
```

解析器最多接受 3 个、每个不超过 2 MiB 的同目录样本，只允许无凭据的 `https://javdb.com/v/<id>` URL，遇到挑战页、必填锚点消失、非法日期或重复来源对象会失败。该命令不联网。

需要验证完整数据闭环时，按仓库根目录 README 的“真实数据闭环”执行。SQLite 数据库、原始页面、下载图片和成果页都位于 `runtime/`。联网命令只接受最多 5 个显式详情 URL，要求 `public_metadata_review_only` 确认、robots 文件和至少 20 秒间隔；失败页面必须进入错误表和对账报告。

预期：Python 32 项、配置 8 项及文档链接检查通过；管线报告 `input_records=43`、`unique_observations=40`、`entities=36`、`conflicts=2`、`invalid=2`。这些 fixture 数字是确定性结果，不能作为真实网站采集数量或准确率；真实闭环的本轮结果见[验收报告](./REAL_DATA_ACCEPTANCE.md)。

预览打开 `http://127.0.0.1:13003/`。执行“来源管理 → 新增网站 → 保存并创建任务”，设置路径、区域和预算。网站任务仍显示待接入；运行、审核、展示闭环选择“本地合成样本”。

网站与任务存在当前浏览器，换电脑不会自动同步。右上角导出 JSON 的 `configuration` 包含网站与计划，可用作配置评审输入；当前没有导入按钮，需要在新浏览器重新登记。Git 同步代码，不同步浏览器状态。

## 4. 可选浏览器回归

```bash
npm ci
npx playwright install chromium
npm run test:collection-demo
```

Linux 若缺浏览器系统库，可根据安装提示补齐；有管理员权限时可使用 `npx playwright install --with-deps chromium`。已经装有兼容的 Chromium 时，可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指向可执行文件，不必重复安装。

预期 21 组检查通过，产出 `docs/evidence/collection-demo/report.json` 与 10 张截图。原仓库中的既有截图证明当时的本机结果，不代替新电脑自己的测试。

## 5. 真实网站连通性和页面样本

此前服务器上 JavDB 直连超时；使用现有代理后 CONNECT 200，但目标 TLS 被重置，未取得正文。Jable 直连超时。这个结果不能外推到新的测试电脑。

先测试来源规则入口，不携带登录 Cookie、账号凭据或浏览器认证状态。需要代理时使用新电脑已有配置；curl 会读取 `HTTPS_PROXY` 等环境变量。示例 `http://127.0.0.1:7890` 只是常见的本机地址写法，必须换成实际代理地址。代理密码留在本机配置中，不写进脚本、Git、截图或反馈。

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force runtime/source-test | Out-Null
curl.exe --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/javdb-robots.txt --write-out "HTTP=%{http_code} bytes=%{size_download}\n" https://javdb.com/robots.txt
curl.exe --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/jable-robots.txt --write-out "HTTP=%{http_code} bytes=%{size_download}\n" https://jable.tv/robots.txt
```

Linux/macOS：

```bash
mkdir -p runtime/source-test
curl --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/javdb-robots.txt --write-out 'HTTP=%{http_code} bytes=%{size_download}\n' https://javdb.com/robots.txt
curl --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/jable-robots.txt --write-out 'HTTP=%{http_code} bytes=%{size_download}\n' https://jable.tv/robots.txt
```

这些命令不自动跟随重定向；3xx 时先核对目标。HTTP 200 还要检查内容是否真为 robots，而非登录页、验证码或错误页。robots 只是访问规则证据之一，还需核对来源条款与拟使用内容的范围。代理 CONNECT 200 不等于目标网页 200；curl 显示目标 `HTTP=000` 表示没有收到目标 HTTP 响应。

仅当页面公开、来源规则允许且访问正常时，人工选择 1–3 个作品详情页。把以下命令中的占位文本换成允许访问的完整 URL：

PowerShell：

```powershell
$samplePageUrl = '替换为已确认允许访问的公开详情页URL'
curl.exe --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/detail-001.html --write-out "HTTP=%{http_code} bytes=%{size_download}\n" "$samplePageUrl"
Get-FileHash runtime/source-test/detail-001.html -Algorithm SHA256
```

bash/zsh：

```bash
sample_page_url='替换为已确认允许访问的公开详情页URL'
curl --connect-timeout 10 --max-time 30 --max-filesize 2097152 --output runtime/source-test/detail-001.html --write-out 'HTTP=%{http_code} bytes=%{size_download}\n' "$sample_page_url"
```

每次人工请求间隔至少 10 秒，本轮只取少量样本；遇到限制不做循环重试。不要获取视频、音频、播放列表、磁力或下载资源。保存的 HTML 仅作解析输入，包含第三方脚本时不要将其作为本地网页直接运行。

| 响应 | 本轮处理 |
| --- | --- |
| DNS / TLS / `HTTP=000` | 记录错误和是否使用代理，不判为解析失败 |
| 3xx | 记录并核对跳转，不直接启用跟随 |
| 401 / 403、登录或验证码 | 停止该来源，记录状态 |
| 429 | 记录限流，不继续这轮取样 |
| 200 但内容为挑战页 / 错误页 | 标为无效样本，不计入资料条数 |
| 200 且真实公开详情内容可读 | 按字段表人工核对并保存样本 |

逐个样本核对：番号、标题、厂牌、人物别名、明确的发行日期。缺少字段保留空值。网页上架/上传日期不能当作作品发行日期。记录真实文字与其页面位置，后续据此编写固定版本解析器，不推测现有选择器已经适用。

## 6. 反馈和数据文件

复制[结果模板](./TEST_RESULT_TEMPLATE.md)到 `runtime/source-test/result.md`，填写代码版本、环境、是否用代理、目标状态、样本 hash、预期字段与错误。`runtime/` 被 Git 忽略。

先回传脱敏的结果说明和字段清单。需要分享 HTML 时确认其允许用于测试，并去除账号信息、Cookie、签名链接、代理凭据和不相关资源引用；不要上传浏览器完整配置、HAR 或登录态。

原始真实页面和真实资料不自动提交 GitHub。可以通过单独提供的测试文件共享方式交付；若以后需要纳入解析器 fixture，应先精简成可分发的最小样本并记录来源与使用范围。

## 7. 更新代码和下一步

保持自己的测试结果在 `runtime/`，更新前先查看本地修改：

```bash
git status --short
git pull --ff-only
npm test
npm run build:collection-demo
```

如果有修改待保留，不使用 `reset --hard` 覆盖。依赖锁文件变化时再运行 `npm ci`。浏览器配置独立于代码，刷新不会把新仓库内容自动导入配置。

有效页面样本取得后，后续开发顺序为：真实来源解析器与 fixture → 版本化候选校验 → 单来源小批量 → Go 接收与人工审核 → 区域调度、增量与恢复。这个阶段完成后才有真实的 `collect` 来源命令；当前手册不承诺网站任务已能自动抓取。
