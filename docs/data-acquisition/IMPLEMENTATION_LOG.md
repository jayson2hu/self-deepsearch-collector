# 真实连接器实施记录

日期：2026-09-16（Asia/Shanghai）。基线 commit：`2fea4051accf1f53d0b3c804a1ba6cfd9ddc7a62`。执行环境：Windows、Node 24.20.0、npm 11.19.0、Python 3.14；使用本机既有代理，仅记录“使用代理”，不记录地址、账号、Cookie 或流量内容。

## 进度

| 步骤 | 状态 | 结果 / 证据 |
| --- | --- | --- |
| 克隆与基线核对 | 完成 | 仓库 HEAD 与交接版本一致，工作树初始干净 |
| fixture 管线与演示构建 | 完成 | 43 输入、40 唯一观测、36 实体、2 冲突、2 无效；单文件演示可重建 |
| JavDB robots / 首页 | 完成 | HTTP 200；robots 要求 20 秒 crawl delay，禁止搜索、标签和 recent 路径 |
| Jable robots / 首页 | 完成后停止 | robots HTTP 200；首页 Cloudflare 403 challenge，不绕过 |
| Jable 演员离线解析 | 代码与测试完成 | 接受公开演员页 HTML 快照，提取姓名、头像和最多 3 张影视图片；SQLite、私有下载与离线预览已接入 |
| JavDB 详情取样 | 完成 | 两个公开 `/v/<id>` 页面均 HTTP 200，请求间隔 20 秒，无媒体下载 |
| JavDB 演员头像 | 完成验收 | 公开 `/actors` 页面解析 39 位演员；39 张头像下载成功，0 失败，格式/尺寸/hash 均记录 |
| JavDB 字段解析器 | 完成首版 | `javdb-html@2026-09-16.1`，输出五字段、hash、幂等键和字段证据 |
| 真实候选校验 | 完成 | 2 个样本 → 2 条有效候选；现有候选校验器再次验证通过 |
| 无痕浏览器回归 | 完成 | Chrome 隔离上下文，21 组检查与 10 张截图通过，无外部请求 |
| SQLite 数据基座 | 完成 | runs、works、observations、aliases、media candidates、errors；重复导入不重复作品/观测 |
| 有界真实联网采集 | 完成验收 | 显式 URL、最多 5 条、robots 证据、20 秒间隔、2 MiB 上限、失败快照与错误表 |
| 图片私有 staging | 完成验收 | 5 张真实封面和 13 张详情图；JPEG 魔数、尺寸、字节数和 SHA-256 均记录，权利仍待审 |
| self-deepsearch 导出 | 完成 staging 合同 | 作品 JSONL 与 `media_staging.jsonl`；正式 4 档 WebP manifest 仍由原媒体处理器生成 |
| 演员导出 | 完成 staging 合同 | `performers.jsonl` 与 `performer_media_staging.jsonl`；头像仍需权利审核及正式 WebP 派生 |
| 真实成果页 | 完成 | 5 条真实作品、18 张本地图片、详情图切换、无外部请求的离线浏览器验收 |
| 生产定时调度 / 自动增量采集 | 未启用 | 条款与权利状态未完成，来源登记继续 `network_enabled=false`；当前仅启用人工触发、显式 URL、有限条数的验收采集 |
| 正式 Go ingest / 审核发布 | 未开始 | 仍需 B0 合同冻结、任务 API、spool、幂等 ingest 和人工审核集成 |

## 样本清单

| 文件 | 来源对象 | bytes | SHA-256 | 已验证字段 |
| --- | --- | ---: | --- | --- |
| `detail-001.html` | `0eEOzX` | 82,960 | `378A00DF24F1E62A474B0ADB64251F1CA5ECAF9A63B8947C73B8D59CAAE388B7` | 番号、标题、发行日期、片商、演员 |
| `detail-002.html` | `0eEX60` | 55,569 | `407A74B5C7CE8302A8C54C9D6FD416DB0154231DBD8B9261E6963F9767FB413B` | 番号、标题、发行日期、片商、演员 |

原始 HTML 与解析后的 `candidates.jsonl` 位于 `runtime/source-test/`，不提交 Git。页面可能包含第三方脚本、表单令牌或资源引用，只作为字节输入解析，不在本地浏览器执行。响应头曾包含匿名临时 Cookie，记录完状态后删除，不进入测试结果或仓库。

## 复现解析

在样本目录创建 `javdb-samples.json`：

```json
{
  "source_id": "javdb_reference",
  "connector_version": "javdb-html@2026-09-16.1",
  "checked_at": "2026-09-16T14:30:22Z",
  "samples": [
    {"file": "detail-001.html", "url": "https://javdb.com/v/0eEOzX"}
  ]
}
```

运行：

```bash
npm run collector -- parse-samples --manifest runtime/source-test/javdb-samples.json --output-dir runtime/source-test/parsed
npm run collector -- validate --input runtime/source-test/parsed/candidates.jsonl
```

解析器限制为最多 3 个样本、单文件不超过 2 MiB、文件不得逃逸清单目录、URL 必须是规范 HTTPS JavDB 详情地址。输出 `parse-report.json` 记录文件 hash、大小、来源对象、字段完整性和解析器版本；输出明确标记 `network_access=false`。

## 下一步

1. 完成来源条款、展示字段使用范围和保存期限审查；未完成前不打开自动联网。
2. 在允许范围内补齐缺失日期、缺失片商、历史页面和结构变化样本，扩充真实样本覆盖。
3. 冻结 B0 JSON Schema、长度/类型限制、批次合同和 Go/Python 一致性用例。
4. 通过人工任务实现单来源小批量 fetcher，强制主机/路径白名单、20 秒间隔、2 MiB 响应上限和总预算。
5. 接入 Go ingest、字段冲突审核和 revision/publication；真实候选不能因解析成功直接公开。

## 2026-09-19：Jable 公开演员采集推进

以上 09-16 的 Jable 访问失败记录保留作为历史。09-19 在同一本机代理下，新建有界面隐私 Chrome 上下文成功取得 `/models/` 和普通数字分页；普通 HTTP 仍可能 403。未修改指纹、导出账号 Cookie 或自动处理验证码。

真实页面发现 214 页分页范围。已实现逐页脱敏保存、检查点、续采、SQLite 幂等入库、仅普通头像下载、连接失败的显式重试、头像缺项审计、分页覆盖核对和 Jable 专用可迁移归档。真实采集不再依赖用户手动导出 HTML。历史解析器的影视图片回退已移除，此管线仅采集普通头像。

验收数据以 `runtime/jable-source/20260919-batch-*/crawl-report.json`、`runtime/jable-preview/data-audit.json`、`coverage-report.json` 和 `browser-report.json` 为准。未采页面、重复记录、来源未提供头像、下载失败分别记录，不把它们隐藏为“完整”。运行方式及保存位置见 [Jable 手册](../JABLE_ACTOR_ACQUISITION.md)。

最终结果：214 页；4,271 次记录出现、75 次跨页重复、4,196 位唯一演员；130 张真实头像全部下载、解码和 hash 核验成功；4,066 位在本次列表中无头像。补采首页后，全部 4,196 位均有列表作品数。归档含 804 个可校验文件，ZIP 位于 `runtime/jable-archive/20260919-jable-actors.zip`。本地代码与手册已落盘，未提交 Git；正式 self-deepsearch 发布仍未接入。
