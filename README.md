# self-deepsearch-collector

幕鉴 / self-deepsearch 的独立采集测试与设计仓库。用于在另一台电脑运行样本管线、配置网站任务、验证来源访问并整理页面样本。

当前已接入固定版本的 **JavDB 真实页面解析器**，并用公开详情页验证番号、标题、发行日期、片商、演员和图片候选；Jable 现已通过隔离浏览器验证公开演员列表采集、普通头像下载、逐页入库、断点续采和可迁移归档。仓库还提供人工触发的有界联网采集、SQLite 数据基座、私有媒体 staging、self-deepsearch 暂存导出和真实数据验收页。生产定时调度、无限增量和 PostgreSQL 正式发布仍未启用；来源条款与权利状态未完成前，`network_enabled` 保持 `false`。可运行能力与真实数据测试步骤见[远端电脑测试手册](./docs/REMOTE_DATA_TEST.md)。

要在另一个项目中同步已验收的 Jable 演员数据，直接把 [Jable 数据跨项目同步执行手册](./docs/JABLE_DATA_SYNC_GUIDE.md) 交给执行者。

## 开始测试

要求：Python 3.12+、Node.js 22+。合成管线和演示构建没有 Python 第三方依赖，不需要 Docker、Go、数据库或生产凭据。

```bash
git clone https://github.com/jayson2hu/self-deepsearch-collector.git
cd self-deepsearch-collector
```

创建 Python 环境：Windows 使用 `py -3.12 -m venv .venv`；Linux/macOS 使用 `python3 -m venv .venv`。无需激活环境，脚本优先使用仓库 `.venv`。

```bash
npm test
npm run build:collection-demo
npm run preview:collection-demo
```

取得允许用于解析测试的 JavDB HTML 后，可用清单离线生成真实候选：

```bash
npm run collector -- parse-samples --manifest runtime/source-test/javdb-samples.json --output-dir runtime/source-test/parsed
npm run collector -- validate --input runtime/source-test/parsed/candidates.jsonl
```

清单格式、样本 hash 与本次实现进度见[真实连接器实施记录](./docs/data-acquisition/IMPLEMENTATION_LOG.md)。解析命令不访问网络，原始 HTML 与真实候选继续留在被 Git 忽略的 `runtime/`。

## 真实数据闭环

```bash
# 解析样本并落入 SQLite
npm run collector -- ingest-samples --manifest runtime/source-test/javdb-samples.json --database runtime/collector.db --output-dir runtime/source-test/ingested

# 有界联网采集：最多 5 个显式详情 URL，强制读取 robots 证据和 20 秒间隔
npm run collector -- collect-review --scope runtime/source-test/live-scope.json --robots-file runtime/source-test/javdb-robots.txt --database runtime/collector.db --output-dir runtime/source-test/live-run

# 私有 staging 封面、平台导出与离线成果页
npm run collector -- download-media --database runtime/collector.db --output-dir runtime/media-staging --purpose display --max-images 20 --acknowledgement internal_review_only
npm run collector -- export-selfdeepsearch --database runtime/collector.db --output-dir runtime/selfdeepsearch-export
npm run collector -- build-showcase --database runtime/collector.db --output-dir runtime/real-showcase
npm run test:real-showcase -- runtime/real-showcase 5
```

本轮真实验收结果、数据库计数、图片标准与遗留边界见[真实数据验收报告](./docs/REAL_DATA_ACCEPTANCE.md)。

## JavDB 演员头像

JavDB 公开演员列表解析、SQLite 入库、头像下载和离线展示已经接入。本轮从一个公开 `/actors` 页面实际取得 39 位演员和 39 张头像，全部下载成功并通过 JPEG、尺寸、字节数和 SHA-256 检查：

```bash
npm run collector -- ingest-javdb-actors --input runtime/source-test/javdb-actors.html --source-url https://javdb.com/actors --checked-at 2026-09-18T00:00:00+08:00 --database runtime/collector.db --output-dir runtime/javdb-actors-ingested
npm run collector -- download-javdb-avatars --database runtime/collector.db --output-dir runtime/performer-media-staging --max-images 50 --acknowledgement internal_review_only
npm run collector -- build-actor-showcase --database runtime/collector.db --output-dir runtime/actor-showcase
```

原始列表 HTML 解析后删除，避免保留临时表单令牌；脱敏后的演员 JSONL、数据库记录、头像 staging 和展示页保留在 `runtime/`。self-deepsearch 导出现在同时生成 `performers.jsonl` 与 `performer_media_staging.jsonl`。

## Jable 公开演员资料与普通头像

2026-09-19 已通过本机代理和全新隔离 Chrome 实际取得公开演员列表及普通头像。普通 HTTP 请求仍可能返回 403，因此保留逐页证据、失败记录和断点；不绕过验证、不读取已有浏览器 Cookie。最新覆盖范围与真实数量以运行报告为准，见 [Jable 采集与保存手册](./docs/JABLE_ACTOR_ACQUISITION.md)。

```bash
# 新目录；最多 20 页，每页至少间隔 10 秒，逐页存入数据库
npm run capture:jable -- --output-dir runtime/jable-source/new-batch --max-pages 20 --database runtime/collector.db

# 按报告续采；新批次必须使用另一个目录
npm run capture:jable -- --output-dir runtime/jable-source/next-batch --max-pages 20 --database runtime/collector.db --resume runtime/jable-source/new-batch/crawl-report.json
```

也可参考[样本清单](./docs/data-acquisition/jable-snapshot-manifest.example.json)导入允许使用的公开 HTML 快照：

```bash
# 从公开 HTML 快照解析演员名称、普通头像和可见的列表作品数
npm run collector -- parse-jable-samples --manifest runtime/jable-source/manifest.json --output-dir runtime/jable-source/parsed

# 写入 SQLite 数据基座
npm run collector -- ingest-jable-samples --manifest runtime/jable-source/manifest.json --database runtime/collector.db --output-dir runtime/jable-source/ingested

# 仅下载已观察到的普通头像，不下载影视封面、露骨图片或视频
npm run collector -- download-jable-media --database runtime/collector.db --output-dir runtime/jable-media-staging --max-images 30 --acknowledgement internal_review_only

# 生成完全离线的演员展示页
npm run collector -- build-performer-showcase --database runtime/collector.db --output-dir runtime/jable-preview

# 生成缺项清单；制作可迁移归档（归档目录必须是新目录）
npm run collector -- audit-jable --database runtime/collector.db --output-dir runtime/jable-preview
npm run collector -- archive-jable --database runtime/collector.db --output-dir runtime/jable-archive/new-snapshot
```

解析器只接受公开 `/models/` 列表、分页及演员页；下载器只接受实际页面观察到的 `assets-cdn.jable.tv/contents/models/` 普通头像。拒绝挑战页、登录信息、外部图片主机和超过 2 MiB 的 HTML。保存文件类型、尺寸、字节数、SHA-256 和源页面；另用离线浏览器验证真实图片解码。全部保持 `needs_review`，不会自动公开发布。

在这台电脑的浏览器打开 `http://127.0.0.1:13003/`。也可以直接打开已提交的 `docs/collection-demo/index.html`，基本交互无需安装依赖。

`npm test`、构建与预览只使用 Node/Python 标准库。浏览器自动测试需要额外执行 `npm ci` 并安装或指定 Playwright Chromium，详见手册。

## 文档与数据

| 入口 | 内容 |
| --- | --- |
| [远端电脑测试手册](./docs/REMOTE_DATA_TEST.md) | Windows/Linux 安装、fixture、代理、来源取样与结果回传 |
| [测试结果模板](./docs/TEST_RESULT_TEMPLATE.md) | 记录代码版本、环境、来源响应与字段样本 |
| [设计与开发入口](./docs/data-acquisition/README.md) | 产品、来源、字段、架构、任务与验收 |
| [网站与任务配置](./docs/data-acquisition/WEBSITE_TASKS.md) | 登记来源、创建有界计划和本地保存 |
| [演示说明](./docs/collection-demo/README.md) | 六页交互与离线打开方式 |
| [原项目导出清单](./SOURCE_MANIFEST.json) | 来源仓库、基线 commit 与本次选择的文件 |
| [独立仓库验证](./docs/HANDOFF_VERIFICATION.md) | 安装、环境、样本与浏览器的实际验证范围 |

样本包括 43 条观测、36 个合成作品、8 个虚构人物和 4 个虚构厂牌。真实网站访问响应和页面样本放在被 Git 忽略的 `runtime/`，与已提交的 fixture 分开。

本仓库独立于正式公开站与 API。页面和项目蓝图不会自动跟随原仓库代码变化；网站配置保存在当前浏览器，不随 `git pull` 同步。后续代码更新通过本仓库 commit 和远端拉取完成。
