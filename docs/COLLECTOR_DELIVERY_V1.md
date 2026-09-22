# 人物候选元数据交接 v1

本仓库负责把现有 SQLite 人物观测转换成主平台 `collector.performers.v1` 批次，并通过 HTTP 投递、保存回执和断点续传。主平台 `self-deepsearch` 负责来源登记、服务凭据、事务暂存、候选审核和正式发布。这两个独立仓库通过版本化合同对接，不自动同步代码。

本阶段只交接人物名称、别名、来源资料 URL、来源内容 hash、观测时间和连接器版本。新候选始终为 `pending`，平台写入 `needs_review / staging / adult_status=unknown`。不下载新资料，不发送头像，不推断成年状态，也不自动创建正式人物或发布内容。正式映射、权利审核、成年确认和发布仍需后续流程。

## 1. 平台准备

平台先登记来源，确认状态为 `active / public_web`，权利状态为 `needs_review` 或 `allowed`，并记录来源 UUID 和网站 URL。来源 active 仅表示允许接收当前元数据，不授权自动采集。

平台 API 配置同一来源 UUID 的 `COLLECTOR_INGEST_SOURCE_ID` 和独立 `COLLECTOR_INGEST_TOKEN`。Token 必须为 32～4096 个非空白 ASCII 字符，不得复用登录、监控或媒体凭据。采集端只通过同名环境变量读取 token，不提供命令行 token 参数，也不写入批次、回执或日志。

远端接口使用 HTTPS，路径固定为 `/internal/v1/collector/performer-batches`。开发联调仅可显式启用 `--allow-loopback-http` 并使用 `127.0.0.1` 或 `::1` 等字面 loopback 地址。客户端禁用 HTTP 重定向及环境代理，避免 Authorization 被发送到其他地址；不会跳过 TLS 证书校验。

## 2. 生成可复现批次

以下 UUID 是示例，必须替换成平台实际登记的来源 UUID。`jable_reference` 是采集 SQLite 的来源 key；它与平台 UUID 的映射由本次命令显式指定。JavDB 或其他来源应使用各自 key、UUID 和来源 URL，不能混用。

```bash
npm run collector -- export-performer-batches \
  --database runtime/collector.db \
  --source-key jable_reference \
  --source-id 12345678-1234-1234-1234-123456789abc \
  --source-url https://jable.tv \
  --region japan \
  --output-dir runtime/performer-delivery/batch-001
```

输出目录必须不存在。命令通过只读 SQLite 连接和事务读取一致快照，不执行数据库迁移或修改业务数据。SQLite 的 WAL 模式可能需要共享内存辅助文件；对必须完全保持原样的归档，先复制数据库到临时目录，并用副本导出。正在写入的数据库应使用 SQLite backup API 制作一致副本，不能直接复制活跃的数据库主文件。

导出按照当前人物记录关联对应 hash 的原始观测，保留别名和连接器版本，按连接器与 external_id 稳定排序；仅导出本地 `needs_review / allowed` 且 `publication_status=staging` 的人物。发现 `restricted / takedown` 或其他发布状态会拒绝整次导出，避免把已知限制降为普通待审核状态；缺失观测、非法时间、跨来源 host、非法字段或空来源也会报错。默认最多 100,000 条，可用 `--max-records` 下调。每批最多 500 条，也可用 `--batch-size` 下调；单批 UTF-8 字节超过 2 MiB 时继续拆分。

输出包含 `batch-001.json` 等文件和 `manifest.json`。每项 manifest 保存文件名、条数、原始字节数、`request_hash` 和幂等键。同一快照及相同参数生成完全相同字节；幂等键随实际批次内容变化。manifest 记录来源映射及总数，不包含本机绝对路径、token 或媒体文件。投递前会校验所有批次，任何一项失败时都不会发送第一批。

## 3. 投递、续传和原样重放

通过本机密钥管理或交互输入，把服务 token 放入 `COLLECTOR_INGEST_TOKEN` 环境变量，不把它写入 shell 历史或版本库。然后执行：

```bash
npm run collector -- deliver-performer-batches \
  --manifest runtime/performer-delivery/batch-001/manifest.json \
  --endpoint https://platform.example/internal/v1/collector/performer-batches \
  --receipts-dir runtime/performer-delivery/batch-001/receipts
```

客户端直接发送已保存的 JSON 原始字节，用 `X-Content-SHA256` 传入 hash。每批成功后，校验服务回执的状态、批次 UUID、hash 和计数，再用临时文件、fsync 和原子替换保存独立回执。回执绑定完整 endpoint、manifest hash 和来源 UUID；同一个回执目录同时只允许一个进程投递，进程退出后操作系统自动释放锁。

同一命令再次运行会跳过有有效回执的批次。首批成功、后续失败时，已保存的回执保持有效。请求已提交但响应丢失时，客户端重发同一字节，平台返回原回执；如果本机在保存回执前退出，下次运行也会安全重放。

使用 `--replay` 强制重发全部原始批次，以验收远端幂等性。不要编辑已有批次文件后重试；要发送更新观测，应重新导出到新目录。远端改用另一套数据库时也应使用新的回执目录，避免本地历史回执使新目标被跳过。

默认最多尝试 3 次，网络中断、429、500、502、503 和 504 使用有界退避重试。`--attempts` 可设为 1～10，`--timeout` 为每次请求超时秒数，最大 120。400、401、403、404、409、重定向以及无效回执直接停止；修正配置后可续传，不删除已成功回执。

成功输出单个 JSON 对象，包括 `records`、`batches`、`requests`、`submitted`、`skipped`、`replayed`。`requests` 包含重试次数；`submitted` 是本轮取得有效回执的批数；`replayed` 是其中 HTTP 200 的批数。`status=staged` 仅代表元数据暂存成功，不代表审核或公开发布通过。

## 4. 验证

```bash
npm test
npm run collector -- test -p test_delivery.py
```

客户端测试使用临时 SQLite 和本机 loopback HTTP 服务，覆盖重复导出、全部批次预检、丢响应重试、部分成功后续传、原样重放、永久错误停止、拒绝重定向、禁用环境代理、跨 endpoint 回执拒绝、回执内容校验与并发目录锁。测试不访问外部采集网站。

跨项目真实数据库验收由主平台仓库的 `scripts/run_collector_e2e.py --output .cache/<新目录> --keep` 执行，读取指定采集仓库的实际 CLI，连接隔离 PostgreSQL 与实际 API 监听端口；完整用法和本轮实测证据以主平台验收文档为准。
