# 幕鉴资料供给：设计与开发入口

独立测试仓库的安装与另一台电脑操作步骤，先读[远端电脑测试手册](../REMOTE_DATA_TEST.md)。本目录保留采集设计上下文，未导出的原平台文件通过固定 commit 的 GitHub 链接引用。

版本 CD v1，2026-09-16。本包响应“设计整个采集任务、为资料展示提供数据支持，并提供演示页面”的新需求，是既有 Release B 计划的增量。当前交付为**具体来源接入设计、可执行的离线样例管线、交互原型和验收记录**；真实来源连接器、区域采集执行器和线上批次导入仍待开发及实测。

## 交付导航

| 文档 / 产物 | 内容 |
| --- | --- |
| [展示与交互设计](./PRODUCT_DESIGN.md) | 用户任务、页面、内容主次、异常与审核交互 |
| [来源研究与准入](./SOURCE_RESEARCH.md) | JavDB / Jable 访问证据、角色假设、接入条件 |
| [数据合同与字段映射](./DATA_CONTRACT.md) | 页面字段、候选证据、日期语义、去重与公开投影 |
| [采集架构与运行设计](./ARCHITECTURE.md) | 七种任务、区域调度、spool、事务、资源与恢复 |
| [开发与验收计划](./DEVELOPMENT_PLAN.md) | 分阶段任务、依赖、验收和当前完成边界 |
| [新增网站与任务](./WEBSITE_TASKS.md) | 网站登记、对应任务计划、浏览器保存与执行边界 |
| [交互演示](../collection-demo/index.html) | 工作概览 / 任务 / 来源 / 审核 / 资料 / 架构 |
| [演示启动说明](../collection-demo/README.md) | 命令、样例操作、截图和测试 |
| [来源登记](../../data/collection/source-registry.json) | 机器可读的来源状态与建议预算 |
| [任务模板](../../data/collection/task-templates.json) | 与既有数据库枚举一致的七种任务 |

## 本地运行

在项目根目录执行：

```bash
npm run build:collection-demo
npm run preview:collection-demo
```

访问 `http://127.0.0.1:13003/`；端口可用 `npm run preview:collection-demo -- --port 13005` 调整。也可以直接打开生成的 `docs/collection-demo/index.html`，本页不依赖网络请求或外部字体。该静态演示与需要同源 API 的旧 `index.html`、Next.js 正式应用不同。

首页点击“运行样例任务”后，在“候选审核”确认一条记录，再到“资料预览”查询同一番号；日期冲突必须选定值，缺少标题无法加入展示。新建 fixture 任务可体验超时重试、结构变化停止和重复批次。“来源管理”支持新增网站及创建对应采集计划，保存在当前浏览器，刷新保留；样例运行与审核状态刷新重置。右上角导出包含配置及演示结果。

## 与既有项目的关系

- 沿用[产品基线](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/PRODUCT_PLAN.md)、[平台架构](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/PLATFORM_ARCHITECTURE.md)、[采集计划](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/COLLECTION_PLAN.md)和[数据库模型](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/docs/DATA_MODEL.md)，不新增常驻服务和数据库迁移。
- 沿用[已记录的重设计决定](https://github.com/jayson2hu/self-deepsearch/blob/3f6c13c020f5b8c6aec81f1fd702a1242889827d/docs/redesign/DECISIONS.md)：查询优先；访客看到资料标题、普通字段及公开更新时间；来源、操作人、冲突与审核仅在内部工作台表达；无真实广告时无占位。
- 旧重设计包将自动采集列为后续工作。本次新需求增加“来源与采集设计、离线开发与演示”的范围，不据此宣称真实自动采集已上线，也不修改既有后台发布授权。
- 公开站仍使用现有 Go API；本演示既不调用正式发布接口，也不写入 PostgreSQL。所有 fixture 人物、作品、厂牌和统计均为虚构。
- `COLLECTION_ENABLED`、部署配置、媒体下载、SEO 对外开放与生产账号规则保持现有运行状态。新增原型不是完整 Release B 验收证据。

本包状态以[开发台账](./DEVELOPMENT_PLAN.md)和[本轮验证证据](./VALIDATION.md)为准。
