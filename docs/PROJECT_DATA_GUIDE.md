# 项目与主要数据指南

后续已增加[全量演员采集与缺项补全](./ACTOR_FULL_COLLECTION.md)。最新可续采工作区为 `runtime/actor-collection/20260922-full/`；下文是首轮归档整理结果，最新联网进展以新工作区的 `collection-summary.json` 为准。

更新：2026-09-22（Asia/Shanghai）。本次从仓库已保存的真实快照提取并整理主要数据；来源数据采集于 2026-09-19（北京时间）。整理日期和来源采集日期分别记录。本次网络检查未收到 Jable/JavDB 的 HTTP 响应，没有取得新的在线记录。

## 项目现在提供什么

本仓库是独立采集与数据交接工具：Node 负责命令入口和浏览器捕获，Python 负责解析、入库、导出与审计，SQLite 保存当前资料和观测历史。设计文档中的 Go API、PostgreSQL、跨区域调度和正式发布属于后续平台接入。

| 目录 | 职责 |
| --- | --- |
| `workers/collector-python/collector/` | JavDB/Jable 解析器、数据合同、SQLite、导出及审计 |
| `scripts/` | 命令包装、隔离浏览器捕获、离线页面与验证 |
| `data/collection/` | 来源登记、任务模板和主要数据索引 |
| `docs/collection-demo/` | 合成演示数据与交互页面 |
| `runtime/jable-archive/20260919-final/` | 随 Git 留存的真实来源快照 |
| `runtime/primary-data/<批次>/` | 从快照重建的独立数据包，可整体迁移 |
| `runtime/source-check/<日期>/` | 当次来源访问检查和失败证据 |

来源/任务演示配置保存在当前浏览器。`network_enabled=false` 表示未启用生产调度；人工有界命令另有范围限制。

## 主要数据与实际缺项

| 数据 | 本次可用数量 | 说明 |
| --- | ---: | --- |
| Jable 当前演员 | 4,196 | 来源 ID 唯一，不按姓名合并 |
| 列表作品数 | 4,196 | 从当前内容观测提取；是列表计数，并非已获取的作品详情 |
| 普通头像 | 130 | 全部存在，合计 880,887 字节，大小及 SHA-256 相符 |
| 演员内容观测 | 4,217 | 变更历史，不能作为当前演员数量 |
| 采集运行 | 228 | 原快照保留的历史运行 |
| 列表分页 | 214 | 历史分批扫描；非原子快照，不保证全站无遗漏 |
| 无头像候选的演员 | 4,066 | 所采列表未提供普通头像，并非下载失败 |
| 本地真实作品详情 | 0 | 当前归档仅含演员；`works.jsonl` 为空是预期结果 |
| 本地 JavDB 数据 | 0 | 历史验收提及的样本和工作库未随仓库保存 |

804 个归档清单文件的大小与 SHA-256 全部核验通过；数据库完整性、外键、JSONL 对账通过。`fixture` 的 43 条合成观测、36 个合成作品独立保存在演示目录，不计入上述真实数据。

## 本次数据存放位置

主目录：`runtime/primary-data/20260922/`。固定来源及本次输出的机器索引见 [primary-data-index.json](../data/collection/primary-data-index.json)。

| 相对主目录的路径 | 用法 |
| --- | --- |
| `collector.db` | 完整工作副本；当前资料、内容历史、采集运行、头像候选 |
| `profiles/performers.jsonl` | 推荐程序读取，含列表作品数、来源页、头像相对路径与 hash |
| `profiles/performers.csv` | 表格查看相同主体字段 |
| `media/` | 130 张普通头像；路径相对于整个主目录 |
| `performers.jsonl` | 演员数据库原始表导出 |
| `performer_observations.jsonl` | 内容观测历史 |
| `performer_media_candidates.jsonl` | 头像候选、文件路径与下载/审核状态 |
| `collection_runs.jsonl` | 历史采集运行 |
| `selfdeepsearch-export/` | 平台暂存交接格式；头像路径查询根目录媒体候选清单 |
| `preview/index.html` | 可直接用浏览器打开的离线搜索与头像预览 |
| `data-audit.json` | 文件检查和逐人头像缺项 |
| `inventory.json` | 来源版本、采集时间范围、整理时间、数量、路径及校验清单入口 |
| `files-manifest.json` | 所生成文件的大小和 SHA-256（清单自身除外） |

原始脱敏列表证据保留在 `runtime/jable-archive/20260919-final/evidence/`，不重复抓取。归整包通过来源清单 hash 关联原归档；需要完整历史证据时连同原归档保存。

## 重建与验收

```bash
# 输出必须是新目录；不覆盖已有数据包。
npm run data:prepare -- --output-dir runtime/primary-data/20260922

# 另存一个批次时使用其他目录名。
npm run data:prepare -- --output-dir runtime/primary-data/20260922-copy

# 验证普通头像的真实解码、搜索、筛选和手机布局。
npm run test:jable-showcase -- runtime/primary-data/20260922/preview
```

整理脚本先校验归档清单，再只读备份数据库，生成独立的数据包。`checked_at` 保留来源检查时间；本次整理时间单独保存。根目录 staging 导出维持现有合同，扩展主体字段放在 `profiles/`。

浏览器测试另外生成 `preview/browser-report.json` 与截图；其结果以当次报告为准，整理命令本身不运行浏览器。整理时生成的文件清单不预先包含之后产生的浏览器报告。

本次实际验收已完成：130 张头像全部解码成功，搜索、筛选、详情和手机布局通过，页面外部请求为 0。综合结果保存在 `runtime/primary-data/20260922/validation-report.json`；本次最终文件清单已补入验收产物，共核验 283 个输出文件。该综合报告由本次验收生成，不是单独运行整理命令的默认产物。

复制或交接时整体移动主目录，保留 `collector.db` 与 `media/` 的相对位置。跨项目幂等接入以 `(source_id, external_id)` 为键，详见 [Jable 同步手册](./JABLE_DATA_SYNC_GUIDE.md)。全部资料仍为 `needs_review / staging`。

## 本次联网检查与后续补采

检查结果位于 `runtime/source-check/20260922/report.json`。沙箱内 DNS 解析失败；在获准直接访问后，两个来源的 `robots.txt` 请求仍遇网络连接错误，均未收到 HTTP 状态。当前没有配置来源代理；本次新增在线记录为 0，不能将网络失败当成网站 403、网站无数据或解析失败。

有可用网络后，沿用[现有 Jable 采集流程](./JABLE_ACTOR_ACQUISITION.md)，每批使用新目录、有限页数和间隔，遇拒绝或挑战停止。JavDB 的恢复需要重新取得允许使用的公开样本。现有已保存快照可独立使用。

一般 `runtime/` 输出不进入 Git；本次脚本、指南和数据索引可随代码保存。历史真实归档是已有的 Git 例外目录，因此其他电脑可以重新运行整理命令生成自己的工作数据。
