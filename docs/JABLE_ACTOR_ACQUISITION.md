# Jable 公开演员资料采集与保存

更新：2026-09-19（Asia/Shanghai）。范围仅为公开职业姓名、来源标识、列表可见作品数和普通头像，不采集成人视频、影视封面或露骨图片。

## 本次实际验收

2026-09-19 01:35（Asia/Shanghai）完成公开列表 214 页扫描，取得 4,271 次演员记录出现，按来源 ID 合并 75 次跨页重复后，保存 **4,196 位演员**。姓名、来源 ID、资料地址和列表作品数字段均已记录；首次首页字段已另行补采。

共发现并下载 **130 张真实普通头像**，130 个不同 SHA-256，全部浏览器解码通过；下载失败 0、待下载 0、损坏/丢失文件 0、SQLite 外键错误 0。其余 **4,066 位在所采列表中未提供头像**，逐人保存在缺项清单，不代表其详情页或其他网站也没有头像。演员详情资料、全站历史记录和生产发布未完成，因此仍标记 `full_site_coverage=false`。

最终成果位于：

- `runtime/jable-preview/index.html`：4,196 位演员的离线预览、姓名搜索、头像筛选及保存信息。
- `runtime/collector.db`：主工作数据库；Jable 内容观测 4,217 条，保留字段更新历史。
- `runtime/jable-archive/20260919-final/`：仅含 Jable 的可迁移数据库、头像、JSONL、self-deepsearch 暂存导出、逐页脱敏证据和验收报告。
- `runtime/jable-archive/20260919-jable-actors.zip`：4,808,213 字节。804 个已列入清单的数据文件均通过打包前及包内 SHA-256 核对，ZIP CRC 正常。

ZIP SHA-256：`fdad731f47b8a48f4bd92e0fe8842e2e4b1ac7fb762d70c10b8096371933864c`。

最终归档重建的离线页面也验证通过：4,196 条演员资料、130 张头像，搜索/过滤/详情/手机布局正常，外部请求为 0。代码测试通过 37 项 Python、3 项浏览器采集规则、8 项配置测试。源码已写入本地项目，未在本次操作中提交或推送 Git。

## 已验证的访问方式

普通代理 HTTP 请求返回过 Cloudflare 403；通过同一本机代理启动系统 Chrome 的全新隔离上下文，可以正常打开公开 `/models/` 及数字分页。未使用隐身指纹插件、验证码代解、账号登录、用户浏览器配置或 Cookie 导出。隐私上下文退出后关闭，不持久化会话。

公开列表实际包含 `<h6 class="title">姓名</h6>`、重复的移动/桌面头像和文字占位。解析器以标题作为姓名，排除占位首字和“部影片”计数；数字分页不会被误当成演员。头像只取页面已提供的地址，不能通过猜测路径补图。

首次观察到最后一页为 214。这个数表示当时的分页范围，不等于完整且静态的全站演员数量：列表会变化，也可能有重复、删改或分页漂移。即使完成分页扫描，仍不能声称所有演员详情和所有历史图片都已取得。

## 运行与续采

先安装仓库依赖及 Python 3.12+、系统 Chrome。用本机环境变量 `HTTPS_PROXY` 指定既有代理；如 Python 自动定位失败，设置 `PYTHON` 为解释器绝对路径。不要在仓库配置里提交代理凭据。

```bash
npm run capture:jable -- --output-dir runtime/jable-source/batch-a --max-pages 20 --database runtime/collector.db
npm run capture:jable -- --output-dir runtime/jable-source/batch-b --max-pages 20 --database runtime/collector.db --resume runtime/jable-source/batch-a/crawl-report.json
```

- 默认显示临时隔离 Chrome；`--headless` 仅供可正常访问的环境选用，不保证无头模式可用。
- 默认每页间隔至少 10 秒，可用 `--interval-seconds 20` 增大间隔。单次默认 5 页，最大 200 页；没有部署定时或无限循环任务。
- 只访问普通数字分页，遵守本次取得的 robots；不点击 robots 禁止的演员选择器或异步排序接口。
- 遇访问验证、拒绝、429、无有效卡片或入库失败停止本批。不会轮换代理或绕过验证。
- 每页完成即保存脱敏 HTML 投影、来源、采集时间、响应 hash、投影 hash、解析清单。原始网页脚本、表单令牌、Cookie、HAR 不落盘。
- `--database` 逐页入库；历史纯采集批次可用下述命令补入。记录按来源 ID 去重，内容 hash 排除采集时间，重复导入不会增加同内容观测。

```bash
npm run collector -- ingest-jable-crawl --report runtime/jable-source/batch-a/crawl-report.json --database runtime/collector.db
```

`crawl-report.json` 中 `visited_pages` 是已保存页面，`pending_pages` 是待采页面，`pages[].stored` 表示该页已由本次进程入库。抓到 HTML 但入库失败时，先用补入命令恢复，再续采。不要同时启动多个网页采集进程。

## 下载、检查与展示

```bash
npm run collector -- download-jable-media --database runtime/collector.db --output-dir runtime/jable-media-staging --max-images 50 --acknowledgement internal_review_only
npm run collector -- build-performer-showcase --database runtime/collector.db --output-dir runtime/jable-preview
npm run collector -- audit-jable --database runtime/collector.db --output-dir runtime/jable-preview
npm run collector -- audit-jable-coverage --report runtime/jable-source/batch-b/crawl-report.json --output-dir runtime/jable-preview
npm run test:jable-showcase -- runtime/jable-preview
```

下载每批最多 50 张、间隔 2 秒、单图最多 10 MiB；不跟随重定向。遇 403/429 停止当前下载批次，剩余图片保留 pending。`download-reports/` 保存历次结果，最新结果在 `download-report.json`。

只有已记录的网络连接或超时失败可通过 `--retry-transient` 重试；不会把 HTTP 403/429 纳入重试队列。来源无头像与下载失败分别记录，不能用其他来源或同名演员图片无标记替换。

`data-audit.json` 检查数据库完整性、外键、文件大小、格式、尺寸和 SHA-256，列出每个头像缺项。`browser-report.json` 另外检查真实图片解码、搜索、过滤、详情、手机布局以及零外部请求。静态页面需要刷新/重新打开才能看到重新生成后的结果。

`coverage-report.json` 沿断点报告链重新读取已保存的脱敏页面，验证每页 hash，统计记录出现次数、去重演员数、跨页重复和未采页。它明确标记为动态列表的非原子扫描，不将“页码扫描完”误写为“全站零遗漏”。

## 留存及后续使用

```bash
npm run collector -- archive-jable --database runtime/collector.db --output-dir runtime/jable-archive/snapshot-a
```

归档目录必须不存在，以免覆盖之前成果。归档包括：

- Jable 专用 `collector.db`，保留采集运行、演员、内容观测和头像候选；不混入其他来源数据。
- 对应 JSONL 导出及普通头像原始文件。
- 相对图片路径、数据库和所有数据文件的大小与 SHA-256 清单。
- 数据审计与缺项清单。归档不能让缺失数据凭空变为完整。

可把整个归档目录搬到其他电脑，再用归档里的 `collector.db` 重新生成演员展示页。原始文件仍放在主工作目录的 `runtime/jable-media-staging/`，展示用副本位于 `runtime/jable-preview/media/`。

仅导出 Jable 数据供 self-deepsearch 后续处理时，可以使用这个仅含 Jable 的归档数据库：

```bash
npm run collector -- export-selfdeepsearch --database runtime/jable-archive/snapshot-a/collector.db --output-dir runtime/selfdeepsearch-jable-export
```

这里的 `performers.jsonl` 和 `performer_media_staging.jsonl` 是暂存交接格式，不是已经审核发布的前端媒体清单。

`runtime/` 的一般运行文件仍被 Git 忽略；本次验收确认的 `runtime/jable-archive/20260919-final/` 作为例外随代码版本保存，其他临时批次、预览副本和压缩包不进入 Git。所有记录维持 `needs_review / staging`；self-deepsearch 正式展示还需要权利审核、标准图片派生及正式接入，本项目的本地预览不是已经上线的生产系统。

## 本次证据位置

首个成功快照：`runtime/jable-source/20260919-browser-01/`。公开第一页及分页发现证据：`runtime/jable-source/20260919-browser-02/`。

后续批次以 `runtime/jable-source/20260919-batch-*/crawl-report.json` 为准。保留失败批次，不把失败删除或计入成功覆盖。最终实际数量见最新 `data-audit.json`、`browser-report.json`、`archive-manifest.json`，不要将单元测试中的合成样本计入真实数据。
