# 本轮开发与演示验证

日期：2026-09-16。范围：CD v1 离线数据管线、静态交互原型、本机预览服务及新增网站/对应任务计划。本次增量包含浏览器本地配置持久化；未启用或验证生产采集、实际来源解析、PostgreSQL ingest、真实审核权限、跨区租约与外部媒体。

## 执行结果

| 检查 | 实际结果 | 说明 |
| --- | --- | --- |
| `npm run build:collection-demo` | 通过 | 43 条输入 → 40 个唯一观测 → 36 个实体；2 组冲突、2 条标题缺失、2 条日期未知 |
| collector unittest | 13 / 13 通过 | 原有 fixture 合同 5 项与新增管线 8 项 |
| Ruff | 通过 | 新增 Python 管线、测试和预览服务 |
| mypy | 通过 | 新增 collector.demo 模块 |
| 网站与任务配置单元测试 | 8 / 8 通过 | 域名、异常地址、字段、来源关联、路径、预算、状态恢复与执行隔离 |
| 浏览器演示验收 | 21 组检查通过 | 桌面 1440px、手机 390px，补充 320px 无整页溢出；独立 file:// 打开 |
| 截图 | 10 张 | 桌面 / 手机工作概览、冲突审核、资料列表、来源管理与任务配置 |
| 本机服务隔离 | 通过 | 仅固定演示资源可读；.env、.git/config、README 与越界路径返回 404 |
| 页面运行时 | 通过 | 无外部资源请求、无 JavaScript 运行错误 |

浏览器检查覆盖：运行样例、未确认禁止加入预览、未裁决冲突禁止确认、选择日期后的查询值、标题缺失禁用、退回不公开、番号全角/空白查询、人物与厂牌关联、输入转义、超时重试、重复不增实体、结构变化停止、任务取消、导出与刷新复位。

新增检查覆盖：网站地址中的凭据拒绝、域名归一与重复拒绝、保存网站后自动选择对应任务来源、目录路径/区域/预算配置、任务状态明确为待接入、页面上无网站运行入口、fixture 运行器跳过网站计划、刷新恢复配置、取消后再次刷新保留状态、配置导出、不可信网站名称按文本呈现、浏览器存储失败保留表单且不宣称成功。测试全过程未向登记的网站发出请求。

Python 管线、Ruff 与 mypy 保留前轮验证结果，本次仅修改演示的 JavaScript 配置与界面。首轮增量浏览器运行在截图阶段遇到一次 Chromium capture 错误；独立截图诊断与完整重跑通过，最终为 21 组检查通过。

手机测试发现表格最小内容宽度导致整页溢出，已通过给 Grid/Flex 容器明确 `min-width:0` 修复，表格在自己的区域滚动。还修正了浏览器测试在首页重复匹配导航入口的问题。最终完整运行结果为通过。

完整机器记录：[report.json](../evidence/collection-demo/report.json)。

## 预览截图

| 页面 | 桌面 | 手机 |
| --- | --- | --- |
| 工作概览 | [桌面概览](../evidence/collection-demo/desktop-overview.png) | [手机概览](../evidence/collection-demo/mobile-overview.png) |
| 候选审核 | [桌面审核](../evidence/collection-demo/desktop-review.png) | [手机审核](../evidence/collection-demo/mobile-review.png) |
| 资料查询 | [桌面资料](../evidence/collection-demo/desktop-catalog.png) | [手机资料](../evidence/collection-demo/mobile-catalog.png) |
| 来源管理 | [桌面来源](../evidence/collection-demo/desktop-sources.png) | [手机来源](../evidence/collection-demo/mobile-sources.png) |
| 任务配置 | [桌面任务](../evidence/collection-demo/desktop-task-plan.png) | [手机任务](../evidence/collection-demo/mobile-task-plan.png) |

## 复现命令

```bash
npm run build:collection-demo
PYTHONPATH=workers/collector-python .venv/bin/python -m unittest discover -s workers/collector-python/tests
.venv/bin/ruff check workers/collector-python/collector/demo.py workers/collector-python/tests/test_demo.py scripts/serve_collection_demo.py
.venv/bin/mypy --follow-imports=silent workers/collector-python/collector/demo.py
npm run test:collection-config
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/installed/chromium npm run test:collection-demo
npm run check:markdown-links
```

本机浏览器测试需要允许启动 loopback 服务和 Chromium。测试脚本只启动临时端口，结束后关闭服务；持续预览使用 `npm run preview:collection-demo`。

## 外部来源结果

用户要求使用代理访问 JavDB 后，已读取并复用本机现有 HTTP 代理。代理 CONNECT 成功，但目标 TLS 握手重置，未获得页面或 robots 正文；Jable 直连超时。证据和结论见[来源研究](./SOURCE_RESEARCH.md)。这是传输结果，不是站点字段验证或采集许可结论。本轮样本全部为合成资料。
