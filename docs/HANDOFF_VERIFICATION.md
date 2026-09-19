# 独立仓库交付验证

日期：2026-09-16。验证位置为独立 `self-deepsearch-collector` 目录，使用新建的 Python 3.12 虚拟环境和本仓库锁文件安装的 Node 测试依赖。

| 检查 | 结果 |
| --- | --- |
| `npm ci --ignore-scripts` | 通过；实际下载并安装 3 个适用当前平台的包，未执行依赖脚本 |
| Python fixture 合同与管线 | 13 项通过 |
| 网站 / 任务配置 | 8 项通过 |
| 文档本地链接 | 通过 |
| `collector demo` 与 `validate` | 通过；43 条输入、40 个唯一观测、36 个实体 |
| `build:collection-demo` | 通过；可重建单文件演示 |
| 浏览器回归 | 21 组通过、10 张截图；仅本机服务及合成资料 |

浏览器结果保存在 [report.json](./evidence/collection-demo/report.json)。使用已有的本机 Chromium 可执行文件，不依赖原仓库的 node_modules 或 Python 环境。未在 Windows/macOS 实机执行；手册给出相应标准命令，跨平台差异仍需在新电脑实测。

本机默认 `python3` 是 3.10，初次执行因版本过低失败；创建独立 Python 3.12 环境后通过。新增 `collector` 包装命令在运行前检查 Python 版本，低于 3.12 时明确报错。另一台电脑请按手册先检查解释器版本。

没有取得真实来源页面，也没有把 fixture 成功算作真实抓取成功。真实网站测试范围、取样方法和结果模板见[远端电脑手册](./REMOTE_DATA_TEST.md)。

## 后续真实来源增量

本交付基线之后，已在 Windows 测试机通过本机代理取得 JavDB / Jable robots、JavDB 首页及公开详情样本。JavDB 页面已由 `javdb-html@2026-09-16.1` 解析并进入 SQLite staging；Jable 首页为 403 challenge 后停止。人工触发、显式 URL、有限条数的 `collect-review` 已完成验收，生产定时调度、自动增量和正式 PostgreSQL 入库仍未启用。完整结果见[真实数据验收报告](./REAL_DATA_ACCEPTANCE.md)与[真实连接器实施记录](./data-acquisition/IMPLEMENTATION_LOG.md)。
