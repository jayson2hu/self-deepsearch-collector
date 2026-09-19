# Jable 演员数据跨项目同步执行手册

本文档用于把 `self-deepsearch-collector` 已验收的 Jable 演员数据同步到另一个项目。执行者应按本文档完成代码接入、数据导入、图片复制或读取、幂等更新和验收，不需要重新访问 Jable 网站。

## 1. 固定数据版本

- Git 仓库：`https://github.com/jayson2hu/self-deepsearch-collector.git`
- 已验收提交：`95cac10a4579a4fd20f15575c2e77a351bb1284b`
- 数据根目录：`runtime/jable-archive/20260919-final/`
- 来源标识：`jable_reference`
- 演员数量：4,196
- 本地头像数量：130
- 数据状态：`needs_review / staging`

执行前必须确认仓库至少包含上述提交：

```bash
git rev-parse HEAD
git merge-base --is-ancestor 95cac10a4579a4fd20f15575c2e77a351bb1284b HEAD
```

第二条命令退出码为 `0` 才能继续。不要从网页预览 DOM、截图或临时 `runtime/jable-preview/` 反向提取数据。

## 2. 数据文件选择

首选使用归档内的 SQLite 数据库：

```text
runtime/jable-archive/20260919-final/collector.db
```

需要批量导入或跨语言交换时使用：

```text
runtime/jable-archive/20260919-final/performers.jsonl
runtime/jable-archive/20260919-final/performer_media_candidates.jsonl
runtime/jable-archive/20260919-final/media/
```

需要按 self-deepsearch 暂存合同接入时使用：

```text
runtime/jable-archive/20260919-final/selfdeepsearch-export/performers.jsonl
runtime/jable-archive/20260919-final/selfdeepsearch-export/performer_media_staging.jsonl
```

不要导入 `performer_observations.jsonl` 作为当前演员列表。该文件是内容变更历史，只用于审计。

## 3. 推荐接入方式

### 3.1 通过 Git submodule 固定版本

在目标项目根目录执行：

```bash
git submodule add https://github.com/jayson2hu/self-deepsearch-collector.git vendor/self-deepsearch-collector
git -C vendor/self-deepsearch-collector checkout 95cac10a4579a4fd20f15575c2e77a351bb1284b
git add .gitmodules vendor/self-deepsearch-collector
git commit -m "chore: add audited Jable actor dataset"
```

目标项目中的数据根目录随后固定为：

```text
vendor/self-deepsearch-collector/runtime/jable-archive/20260919-final/
```

如果目标项目不接受 submodule，可以复制整个 `20260919-final` 目录到目标项目的版本化数据目录。必须整体复制，不能只复制 `collector.db`，否则头像相对路径会失效。

## 4. 当前数据读取规则

演员当前记录来自 `performers` 表。头像通过 `performer_id` 连接 `performer_media_candidates`：

```sql
SELECT
  p.performer_id,
  p.source_id,
  p.external_id,
  p.name,
  p.profile_url,
  p.current_content_hash,
  p.rights_status,
  p.publication_status,
  p.checked_at,
  m.media_candidate_id,
  m.local_path,
  m.sha256,
  m.mime_type,
  m.width,
  m.height,
  m.byte_size,
  m.review_status
FROM performers p
LEFT JOIN performer_media_candidates m
  ON m.performer_id = p.performer_id
 AND m.purpose = 'avatar'
 AND m.download_status = 'downloaded'
WHERE p.source_id = 'jable_reference'
ORDER BY p.name;
```

`local_path` 是相对于数据根目录的路径。例如：

```text
media/091e4a7baf411625216f3d5effa726596b34f467af2856c937a24200311094c6.jpg
```

实际头像路径必须按以下方式拼接：

```text
<数据根目录>/<local_path>
```

不要把 `candidate_url` 当作前端图片地址。目标项目应使用已保存的本地头像，或将本地头像上传到自己的对象存储后使用新地址。

## 5. 字段映射

目标项目字段名可以不同，但含义必须保持一致：

| 源字段 | 目标含义 | 规则 |
| --- | --- | --- |
| `performer_id` | 稳定演员 ID | UUID；推荐直接作为外部实体 ID |
| `source_id` | 来源 | 固定为 `jable_reference` |
| `external_id` | Jable 来源 ID | 与 `source_id` 组成来源唯一键 |
| `name` | 演员显示名 | 不得用文件名或页面首字替代 |
| `profile_url` | 来源资料地址 | 仅作来源追溯，不应由前端自动请求 |
| `current_content_hash` | 当前内容版本 | 用于判断是否需要更新 |
| `checked_at` | 来源检查时间 | 不是业务创建时间或出生日期 |
| `rights_status` | 权利状态 | 当前均为 `needs_review` |
| `publication_status` | 发布状态 | 当前均为 `staging` |
| `local_path` | 本地头像相对路径 | 为空表示本次列表没有取得头像 |
| `sha256` | 头像文件校验值 | 复制或上传后必须复核 |
| `width` / `height` | 原图尺寸 | 当前头像通常为 125×125，不要伪造高清尺寸 |

目标数据库必须建立以下唯一约束之一：

```text
UNIQUE(source_id, external_id)
```

或直接使用稳定的 `performer_id` 作为唯一键。不要仅按姓名去重，同名演员不能自动合并。

## 6. 幂等同步算法

同步程序必须能够重复执行，不能每次生成新演员：

1. 读取一条演员记录。
2. 使用 `(source_id, external_id)` 查找目标记录。
3. 不存在时创建，保存源 `performer_id` 和 `current_content_hash`。
4. 已存在且 `current_content_hash` 相同时跳过业务字段更新。
5. hash 不同时更新姓名、资料地址、检查时间和来源 hash，但保留目标项目自身的审核信息。
6. 头像存在时，用 `media_candidate_id` 或头像 `sha256` 幂等处理媒体。
7. 头像缺失时保留演员记录，不能丢弃演员，也不能从其他网站无标记补图。
8. 不得因为源数据为 `staging` 而自动设置为目标项目的已发布状态。

目标项目若维护来源表，建议记录：

```text
source_id = jable_reference
source_commit = 95cac10a4579a4fd20f15575c2e77a351bb1284b
dataset_path = runtime/jable-archive/20260919-final
synced_at = 本次同步时间
```

## 7. 头像处理

本次只有 130 位演员带有已保存头像，其余 4,066 位必须正常导入但显示头像缺省状态。

复制或上传头像时必须：

1. 从归档根目录解析 `local_path`。
2. 验证文件存在。
3. 计算 SHA-256，并与记录中的 `sha256` 比较。
4. 验证 MIME 为 `image/jpeg`，实际尺寸与 `width`、`height` 一致。
5. 目标文件名建议使用 `media_candidate_id`，不要使用演员姓名作为唯一文件名。
6. 保存原始 hash、源媒体 ID和目标存储键之间的映射。

不得执行以下操作：

- 根据 URL 规律猜测缺失头像；
- 把影视封面当成演员头像；
- 把其他网站的同名演员图片静默替换进来；
- 在未审核时把 `needs_review` 改为 `allowed`；
- 拉伸 125×125 图片并声称是原始高清头像。

## 8. self-deepsearch 暂存导入

若目标项目已有正式 ingest API，优先把 JSONL 转换为该 API 的批次请求，不要让前端直接解析整个 4,196 行文件。

演员文件与媒体文件的关联键是：

```text
performers.jsonl.performer_id
    =
performer_media_staging.jsonl.entity_id
```

媒体记录中的 `candidate_url` 仅用于来源证据。本地文件应从归档根目录下的 `media/` 获取；可通过 `media_candidate_id` 查找同名 JPEG，或者读取根目录的 `performer_media_candidates.jsonl.local_path`。

建议导入顺序：

1. 来源登记；
2. 演员实体；
3. 演员来源映射；
4. 本地头像上传或复制；
5. 媒体 staging 记录；
6. 审核队列；
7. 内部预览；
8. 人工批准后再发布。

## 9. 验收标准

同步完成后必须输出一份报告，并满足：

```text
源演员数                    = 4196
目标成功写入或幂等命中数      = 4196
来源唯一键冲突                = 0
源头像候选数                  = 130
成功校验并同步头像数            = 130
头像 SHA-256 不一致            = 0
头像文件缺失                  = 0
无头像演员数                  = 4066
错误把无头像演员丢弃的数量       = 0
自动公开发布数量                = 0
```

再次运行同一版本同步后还必须满足：

```text
新增重复演员 = 0
新增重复媒体 = 0
演员总数不变
头像总数不变
```

抽样检查至少包括：

- 10 位有头像演员：姓名、来源 ID、图片可解码、hash 相符；
- 10 位无头像演员：演员仍存在，前端使用明确的缺省图；
- 中文、日文、拉丁字母名字均能搜索；
- 前端不直接请求 Jable 图片域名；
- 页面不展示采集证据中的本机绝对路径。

## 10. 源数据完整性检查

在 collector 仓库中可以运行：

```bash
npm ci
npm run collector -- audit-jable \
  --database runtime/jable-archive/20260919-final/collector.db \
  --output-dir runtime/jable-sync-check
```

预期关键结果：

```text
performers = 4196
downloaded_avatars = 130
failed_downloads = 0
pending_downloads = 0
corrupt_or_missing_files = []
sqlite_integrity = ok
foreign_key_errors = 0
```

归档本身还包含：

```text
archive-manifest.json
data-audit.json
coverage-report.json
browser-report.json
```

如完整性检查不通过，应停止导入并报告具体文件，不得跳过错误后宣称同步完成。

## 11. 后续数据版本更新

后续 collector 仓库出现新数据提交时：

1. 更新 submodule，但先记录原提交号；
2. 查找新的版本化归档目录，不覆盖本次 `20260919-final`；
3. 验证新归档清单和数据库；
4. 以 `(source_id, external_id)` 和 `current_content_hash` 做增量同步；
5. 生成新增、更新、未变、媒体新增、错误和缺项统计；
6. 通过验收后再更新目标项目锁定的 submodule 提交。

执行者最终应提交：同步实现代码、数据库迁移（如需要）、自动化测试、同步报告和目标项目内的使用说明。
