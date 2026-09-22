# 全量演员采集与缺项补全

更新：2026-09-22。范围为 Jable/JavDB 公开演员资料、列表可见元数据和实际观察到的普通头像。以 `(source_id, external_id)` 为身份键，不用同名匹配替代身份确认。

## 工作目录与任务

`runtime/actor-collection/20260922-full/` 是本次可续采工作区，从已校验的 `runtime/primary-data/20260922/` 创建独立数据库和头像副本。历史归档及此前交付的数据包不被覆盖。

| 文件 | 内容 |
| --- | --- |
| `collector.db` | 当前演员、原始内容观测、头像候选 |
| `tasks.db` | 全量任务、状态、重试次数、证据位置、来源停止原因 |
| `task-config.json` | 本次来源种子页和最小请求间隔 |
| `task-report.json` | 人物数量、队列状态和各字段缺项 |
| `exports/performers.jsonl`、`performers.csv` | 合并已知字段的演员资料及逐字段来源证据 |
| `exports/missing-fields.jsonl` | 每位演员的待补信息 |
| `media/` | 校验过的普通头像，路径相对工作目录 |
| `runs/<批次>/` | robots、响应 hash、脱敏候选、入库结果及错误 |

初始队列 **4,201 项**：Jable 列表入口 1 项、现有演员详情 4,196 项、JavDB 四类列表入口 4 项。抓取列表后继续登记观察到的分页、新演员和头像。任务数量不是静态全站总数。

初始数据为 4,196 位演员、130 张可校验普通头像、4,066 位缺头像。别名、简介、生日、身高等未被既有列表观察到的字段继续为空，不用推断值补齐。当前进展以工作区报告为准。

## 使用命令

```bash
# 建立工作区；重复执行只补登记任务，不重置成功/阻塞状态。
npm run collector -- plan-actor-tasks \
  --seed-dir runtime/primary-data/20260922 \
  --output-dir runtime/actor-collection/20260922-full

# 每批最多 50 项、600 秒；先验证 robots，来源失败后停止该来源。
npm run collector -- run-actor-tasks \
  --work-dir runtime/actor-collection/20260922-full \
  --max-tasks 50 --max-seconds 600

# 网络可用或错误修复后，显式恢复阻塞来源。
npm run collector -- run-actor-tasks \
  --work-dir runtime/actor-collection/20260922-full \
  --transport browser --retry-blocked --max-tasks 50 --max-seconds 600

# 查看缺项，重建主体 JSONL/CSV。
npm run collector -- report-actor-tasks \
  --work-dir runtime/actor-collection/20260922-full
```

配置见 [actor-tasks.json](../data/collection/actor-tasks.json)，规划命令支持 `--config`。Jable 至少间隔 10 秒、JavDB 至少 20 秒；robots 更严格时采用更长间隔。请求时间保存在 SQLite，跨批次仍限速。

长批次可增加 `--max-tasks`、`--max-seconds`，最大分别为 10,000 项和 43,200 秒。每页入库后确认任务；再次运行跳过成功项。生产定时调度未开启，网页中的任务配置仍是独立演示计划。

## 断点与字段规则

- 队列使用操作系统进程锁。崩溃遗留 `running` 任务可恢复；保存过且 hash 相符的候选原样补入库，不重抓来源。
- 旧 `capture:jable` 已修复：先验证历史投影、补入失败页，再推进页码；缺证据不会静默跳过。旧版快照仍能重放。
- JavDB 保留没有头像的演员；同内容跨时间重采不会增加重复内容观测。
- 详情只提取请求演员，排除推荐人物和作品区。不能确认的姓名不从 URL、OG 或营销标题猜测。
- 每页原始候选独立保存。主体导出按字段读取最新非空观测；详情缺少列表作品数时保留旧列表值及原来源时间。
- `current_content_hash` 仍指实际原始观测；`field_provenance` 分别保存各字段 URL、检查时间、观测 hash 和解析器版本。
- 身高等保留来源原文及单位。未明确提供的生日、别名等继续缺失，不从照片推断身份或年龄。
- 头像必须有来源候选且通过本地 hash/格式/尺寸检查才算已取得；丢失文件和历史失败候选可进入补采任务。

HTTP 模式读取标准代理环境变量。浏览器模式使用全新临时 Chrome 上下文，阻断图片/视频子资源和站外导航，不持久化会话；页面原始 HTML 只在内存中解析。脚本本身不读取系统服务凭据。403、验证挑战、robots 拒绝、结构变化和入库异常会记录错误并停止对应来源。

本次已取得用户明确授权，允许在采集进程中复用系统服务现有认证代理；凭据没有保存到项目。连通测试与任务结果分别位于 `runtime/source-check/20260922-proxy/` 和工作区 `runs/`，不能把 robots 成功当作演员数据采集成功。

本次实际取得 2 页、40 次演员记录出现，更新了 2 位演员的列表作品数；新增演员 0、新头像 0，内容观测总数由 4,217 增至 4,219。Jable 后续分页返回 403，JavDB 经代理连接被重置（errno 104），两来源已停止。现存队列 4,414 项，其中完成 2、阻塞 2、待处理 4,410；未完成全量资料和头像补齐。细节见工作区 `collection-summary.json` 与 `task-report.json`。

代码验收通过：86 项 Python 测试、8 项配置测试、9 项浏览器采集规则与恢复测试。原归档 804 个清单文件再次核验一致，当前数据库完整性和外键检查通过。

平台交接仍使用 [collector.performers.v1](./COLLECTOR_DELIVERY_V1.md) 的已有命令与合同。队列不会自动投递或发布。
