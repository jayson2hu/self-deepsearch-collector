# self-deepsearch-collector

幕鉴 / self-deepsearch 的独立采集测试与设计仓库。用于在另一台电脑运行样本管线、配置网站任务、验证来源访问并整理页面样本。

**当前真实网站解析器尚未接入。** Python CLI 只接受 fixture；网页中为 JavDB、Jable 或新增网站保存的是任务计划，不能自动抓取或写入正式平台。可运行能力与真实数据测试步骤见[远端电脑测试手册](./docs/REMOTE_DATA_TEST.md)。

## 开始测试

要求：Python 3.12+、Node.js 22+。合成管线和演示构建没有 Python 第三方依赖，不需要 Docker、Go、数据库或生产凭据。

```bash
git clone https://github.com/jayson2hu/self-deepsearch-collector.git
cd self-deepsearch-collector
```

创建 Python 环境：Windows 使用 `py -3.12 -m venv .venv`；Linux/macOS 使用 `python3 -m venv .venv`。无需激活环境，脚本优先使用仓库 `.venv`。

```bash
npm test
npm run build:collection-demo
npm run preview:collection-demo
```

在这台电脑的浏览器打开 `http://127.0.0.1:13003/`。也可以直接打开已提交的 `docs/collection-demo/index.html`，基本交互无需安装依赖。

`npm test`、构建与预览只使用 Node/Python 标准库。浏览器自动测试需要额外执行 `npm ci` 并安装或指定 Playwright Chromium，详见手册。

## 文档与数据

| 入口 | 内容 |
| --- | --- |
| [远端电脑测试手册](./docs/REMOTE_DATA_TEST.md) | Windows/Linux 安装、fixture、代理、来源取样与结果回传 |
| [测试结果模板](./docs/TEST_RESULT_TEMPLATE.md) | 记录代码版本、环境、来源响应与字段样本 |
| [设计与开发入口](./docs/data-acquisition/README.md) | 产品、来源、字段、架构、任务与验收 |
| [网站与任务配置](./docs/data-acquisition/WEBSITE_TASKS.md) | 登记来源、创建有界计划和本地保存 |
| [演示说明](./docs/collection-demo/README.md) | 六页交互与离线打开方式 |
| [原项目导出清单](./SOURCE_MANIFEST.json) | 来源仓库、基线 commit 与本次选择的文件 |
| [独立仓库验证](./docs/HANDOFF_VERIFICATION.md) | 安装、环境、样本与浏览器的实际验证范围 |

样本包括 43 条观测、36 个合成作品、8 个虚构人物和 4 个虚构厂牌。真实网站访问响应和页面样本放在被 Git 忽略的 `runtime/`，与已提交的 fixture 分开。

本仓库独立于正式公开站与 API。页面和项目蓝图不会自动跟随原仓库代码变化；网站配置保存在当前浏览器，不随 `git pull` 同步。后续代码更新通过本仓库 commit 和远端拉取完成。
