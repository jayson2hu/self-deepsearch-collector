# 从页面到来源的数据合同

本轮不修改正式 OpenAPI 或数据库。实际数据库字段依据 [v2 目录迁移](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/db/migrations/00002_collector_catalog.sql)、[v14 字段证据迁移](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/db/migrations/00014_content_provenance.sql)与[模型清单](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/docs/DATA_MODEL.md)。下面列出的新增接收接口、字段证据扩展和公开更新时间规则属于下一阶段合同设计。

## 1. 展示字段映射

| 展示字段 | 采集候选 / 归一 | 入库与审核 | 公开规则 |
| --- | --- | --- | --- |
| 番号 | `payload.canonical_code`；NFKC、去空白、统一大小写与分隔符 | `platform.works.canonical_code` / `compact_code`；保留原值为别名 | 必填，不能当实体唯一主键 |
| 标题 | `title`、可选 `title_original` | 作品版本；仅纯文本，有长度上限 | 必填；不拼接站点营销文案 |
| 发行日期 | 明确含义的 `release_date`，ISO 日期或 null | 与来源页面时间、采集时间分开 | 未知显示未知，不参与“最新发行”优先位置 |
| 厂牌 | `studio_name`，原名与规范名 | 厂牌实体候选、作品关系 | 同名需人工选择，不靠字符串直接关联 |
| 人物 | `performer_aliases`、公开艺名 | 人物/别名候选；人物身份、成年状态与作品关系确认 | 未确认身份不创建已发布人物关系 |
| 标签 | 来源原词→受控词典候选 | 有独立词典版本和映射证据 | 不把来源未知分类原样作为本站标签 |
| 图片 | 图片候选元信息；本轮预算为 0 | 既有媒体 manifest、审核与派生流程 | 没有获准资产就用默认图；不热链来源图片 |
| 资料更新时间 | 公开资料版本实际变更事件 | 发布服务生成 / 回填可追溯版本时间 | 不使用 `checked_at`、`received_at` 或探测时间替代 |
| 来源与核验 | `provenance`、字段路径、原值、hash | `collector` 及内部字段证据 | 仅后台；访客详情不增加来源核验面板 |

人物身材参数、社交账号、简介及图片不列首批必填；明确有用且可核验后再扩展。禁止从图片推断身份或成年状态。

## 2. 候选与批次

复用[现有候选校验器](../../workers/collector-python/collector/contracts.py)：`external_id`、`entity_type`、`operation=upsert`、`idempotency_key`、`content_hash`、`payload`、`provenance`。hash 使用 UTF-8、按键排序、无多余分隔空格的 canonical JSON 的 SHA-256。

现有校验器仅为骨架，不具有生产输入的完整类型/长度/URL/时间验证。B0 后续须固定版本化 JSON Schema、正反例和 Go/Python 一致性测试；hash 合法不代表字段事实正确或内容可发布。

新批次合同建议包含：`schema_version`、`batch_id`、`source_id`、`region`、`job_id`、`lease_token`、`connector_version`、`idempotency_key`、`request_hash`、计数、记录数组和 `cursor_after`。一个批次只属于一个来源；多来源结果不能混装后写入同一个 source_id。压缩前后字节上限、记录上限、错误反馈格式均需同时冻结。

本演示 `candidates.jsonl` 附带 `source_id` 和 `demo_entity_id`，用于在本地明确指定合成实体关联；`demo_entity_id` **不是生产协议字段或真实实体匹配算法**。本轮生成的多来源 JSONL 不可直接作为生产 ingest 批次。原有 `collector.cli` 继续仅接收自己的 `fixture@v1`。

## 3. 四种时间

| 时间 | 含义 | 无证据时 |
| --- | --- | --- |
| `release_date` | 作品实际发行日 | null |
| `source_updated_at` | 来源明确标注的资料更新时间 | null；不能用本次抓取时间填充 |
| `checked_at` / `received_at` | 来源核验 / 本平台接收时间 | 由执行或接收服务生成 |
| 公开资料更新时间 | 当前公开版本内容实际改变的时间 | 缺乏历史版本证据则按已批准迁移规则显示未知 |

重复采集、无变化的 hash 对账、来源暂时不可达均不改变公开更新时间。原型中固定样本时间用于初始化资料；本机确认新候选使用浏览器当前时间，仅表达模拟动作。

## 4. 幂等、匹配与冲突

1. 批次：唯一幂等键 + 请求 hash。重试同键同内容返回原回执；同键不同内容返回 409，不部分覆盖。
2. 观测：复用 `UNIQUE(source_id, entity_type, external_id, content_hash)`；重复轮询不新增同一观测。
3. 来源对象：外部 ID 对同一个来源稳定，不跨站共用。新 hash 形成新观测，不抹除旧证据。
4. 作品匹配：番号规范值 + 厂牌 + 发行日 + 已确认人物 + 来源关联综合生成匹配候选；同番号不自动合并。人物别名相同也不等于同一人。
5. 字段冲突：保留当前人工值与各来源候选。人工确认优先；来源优先级只辅助排序，不能触发“最后写入覆盖”。空候选不清除既有值。
6. 裁决：记录字段、选择值、理由、证据和操作者；正式资料变更仍走 revision → 独立审核 → publication。裁决冲突记录本身不隐式发布。

演示数据允许未知日期，但缺标题被隔离。生产拒绝分为合同错误（整个批次或逐行策略需冻结）、字段缺失（候选不可发布）、语义冲突（人工裁决）和关系不确定（候选关联）。不得用同一“成功”覆盖这些情况。

## 5. 字段证据与类型适配

内部字段证据最少包括实体候选 ID、字段名、原始值、规范值、source_record_id、来源类型、允许记录的 URL、页面标题、定位路径或片段 hash、核验时间、解析器版本、置信度及权利状态。来源 URL 可空，但来源类型与核验信息不可伪造。

当前 collector 的 `public_web/official_api/rss/sitemap/fixture` 与平台字段证据的 `web_page/search_result/official/other/manual_csv/legacy_record` 并非同一枚举。集成时显式转换：网页→`web_page`；经验证的官方来源→`official`；RSS/sitemap 作为发现方式保留在 collector，字段事实按实际证据分类。fixture 不得转换成真实来源后发布。需一并修改源 OpenAPI、生成类型和正反例，不能只改前端类型。

## 6. 数据产物

- [来源登记](../../data/collection/source-registry.json)：真实来源 `network_enabled=false`、未验证字段列表为空。
- [任务模板](../../data/collection/task-templates.json)：仅任务设计，`enabled=false`。
- [原始样本 JSONL](../collection-demo/candidates.jsonl)：43 条观测，来源全部为 `fixture`。
- [派生展示数据](../collection-demo/dataset.json)：36 个实体、字段证据、问题、初始展示 ID 和报告，由[离线构建模块](../../workers/collector-python/collector/demo.py)生成。

关系、字段完整度和初始展示集合均可重建；本演示不会创建真实 `media_assets`、资料版本、搜索统计或访问历史。
