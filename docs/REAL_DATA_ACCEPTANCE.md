# 真实数据闭环验收报告

日期：2026-09-16（Asia/Shanghai）。代码基线：`2fea4051accf1f53d0b3c804a1ba6cfd9ddc7a62` 加本轮工作树。目标：验证“真实公开页面 → 解析 → SQLite → 图片 staging → self-deepsearch 导出 → 本地成果展示”。

## 1. 验收结论

闭环已实际运行并通过本地验收：数据库保存 5 条真实作品、5 个唯一观测、11 个人物别名、59 个图片候选，以及 5 张封面和 13 张详情图。成果页使用 18 张本地图片，无热链和外部请求；Chrome 隔离上下文验证全部图片均完成解码。

这不是生产发布批准。所有作品和图片仍为 `staging / needs_review`；`publication_ready_media=0`。要让正式 self-deepsearch 对外展示，还必须完成来源/图片权利审核、由原 `media-python/1` 生成私有 master 与三档 WebP、上传 S3/北京副本，再通过平台审核与 publication。

## 2. 数据与对账

| 项目 | 实际结果 |
| --- | ---: |
| SQLite `works` | 5 |
| 唯一 `work_observations` | 5 |
| 人物别名 | 11 |
| 图片候选 | 59 |
| 符合平台展示槽位的图片候选 | 18 |
| 已下载私有 staging 图片 | 18（5 张封面、13 张详情图） |
| 字段完整作品 | 4 / 5 |
| 正式可发布图片 | 0 |
| 浏览器外部请求 | 0 |

一个作品缺少人物字段，保留为未知，没有推测或补造。首次三页联网范围中，一个 URL 返回 302，一个西方番号不符合首版规则；两项均进入错误表。后者经保留的原始快照修正规范化规则后成功入库；302 仍作为明确失败证据保留。最终单页验收范围为 `discovered=1 / fetched=1 / parsed=1 / stored=1 / omitted=0`。

## 3. 图片标准核对

18 张本地图片均为 JPEG，已记录每个文件的 SHA-256、实际宽高和字节数；其中 5 张封面宽度均为 800px，13 张详情图覆盖缩略图与较高分辨率样例。采集器输出的 `media_staging.jsonl` 对齐原项目 `collector.media_staging` 所需语义：资料 UUID、来源对象、候选 URL、来源页面、用途、位置、权利/下载/审核状态、hash、MIME、尺寸、字节和核验时间。

原平台的正式媒体合同已核对：

- 作品主图：`cover / position=0 / is_primary=true`。
- 作品精选图：`gallery / position=1..3 / is_primary=false`。
- 正式 manifest：一个私有 `master` 和公开 `w320/w640/w960`，全部 WebP。
- 单对象不超过 10 MiB，尺寸 1–20,000px，必须有 SHA-256。
- `rights_status=allowed` 且资产、对象、关系和父资料均 published 才能公开。

## 4. 产物

以下真实数据产物位于被 Git 忽略的 `runtime/`：

| 产物 | 路径 |
| --- | --- |
| SQLite 数据库 | `runtime/collector.db` |
| 原始页面与联网报告 | `runtime/source-test/` |
| 私有封面 staging | `runtime/media-staging/` |
| self-deepsearch 作品/媒体导出 | `runtime/selfdeepsearch-export/` |
| 离线真实成果页 | `runtime/real-showcase/index.html` |
| 浏览器验收报告 | `runtime/real-showcase/browser-report.json` |
| 验收截图 | `runtime/real-showcase/acceptance.png` |
| 演员头像 staging | `runtime/performer-media-staging/` |
| 演员头像展示页 | `runtime/actor-showcase/index.html` |

## 4.1 演员头像增量

2026-09-18 通过本机代理访问 JavDB robots 未禁止的公开演员列表页，实际解析 39 位演员和 39 条头像候选。39 张头像全部下载成功，均为可解码 JPEG，记录真实尺寸、字节数与 SHA-256；离线浏览器验证 39/39 图片解码成功、外部请求为 0。原始页面包含临时表单令牌，完成解析与 hash 记录后已删除，不进入仓库或展示产物。

演员数据与作品数据共用 SQLite 数据基座，但分别保存为 `performers / performer_observations / performer_media_candidates`，不会把作品图片误标为人物头像。导出目录新增 `performers.jsonl` 和 `performer_media_staging.jsonl`；全部头像仍为 `staging / needs_review`。

## 5. “不遗漏”的实现方式

项目不承诺第三方网站在未来永远零遗漏，而是提供可验证闭环：显式 URL 数量作为 `discovered`，逐项记录 `fetched / parsed / stored`，差额写入 `omitted`；HTTP、解码和解析错误进入 `collection_errors`；原始成功响应在解析前保存；来源对象、观测和图片 URL 均有唯一键；重复运行不重复创建作品或同 hash 观测；来源全部图片候选保留，超出正式展示槽位的图片标记 `display_eligible=false` 而不是删除。

正式批量上线前仍需加入列表分页游标、历史窗口和站点级覆盖率评估。当前验收证明的是有界显式范围内没有静默遗漏。
