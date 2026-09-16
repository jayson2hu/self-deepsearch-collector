# 数据工作台交互演示

由 [collector.demo](../../workers/collector-python/collector/demo.py) 生成合成元数据，再由 [构建脚本](../../scripts/build_collection_demo.mjs) 注入 [HTML 模板](../../scripts/templates/collection-demo.html)。生成文件无需 CDN、图片源或 API，支持直接打开和本地服务预览。

```bash
npm run build:collection-demo
npm run preview:collection-demo
# http://127.0.0.1:13003/
```

`-- --port 13005` 可调整端口。服务固定绑定 127.0.0.1，只返回 index.html、dataset.json、candidates.jsonl、robots.txt 和 healthz；不会把项目根目录、.env 或内部文档作为静态目录公开。不要将该设计原型当作正式运营后台部署。

## 在另一台电脑访问

上面的 `127.0.0.1` 指运行项目的服务器。使用另一台电脑时，在那台电脑的终端（Windows 可使用 PowerShell）建立 SSH 转发：

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:13003:127.0.0.1:13003 登录用户@服务器地址
```

把 `登录用户@服务器地址` 替换成平时 SSH 登录项目服务器使用的目标；SSH 使用非默认端口时追加 `-p SSH端口`。保持终端连接，在自己的电脑浏览器打开 `http://127.0.0.1:13003/`。正常建立转发后终端可能没有输出。服务器上的预览服务也需要保持运行。

如果电脑上的 13003 已占用，把 `-L` 后面的第一个 `13003` 改为 `13005`，浏览器改用 `http://127.0.0.1:13005/`；最后一个 `13003` 仍是服务器端口。

也可以只下载单个 HTML，在自己的电脑离线打开：

```bash
scp 登录用户@服务器地址:/root/project/pre/self-deepsearch/docs/collection-demo/index.html ./mujian-demo.html
```

下载完成后双击 `mujian-demo.html` 即可。离线版的复制番号功能可能受浏览器权限限制，其他演示交互不依赖服务器。

## 推荐演示路径

1. 工作概览查看输入→观测→实体→展示四种计数。
2. 点击“运行样例任务”；在候选审核按番号定位记录（列表包含 DEMO-025 至 DEMO-036）。
3. DEMO-025：确认资料与关系，加入预览，作品搜索 `demo025` 能找到它。
4. DEMO-033：先尝试确认，再选择发行日期与确认复核；未选值不能加入展示。
5. DEMO-035：缺少标题，确认按钮禁用，可退回。
6. 再运行同一 fixture，实体不会重复，已作出的决定保留。
7. 采集任务中新建“连接超时”或“结构变化”场景，分别体验重试与停止。
8. 资料预览切换作品 / 人物 / 厂牌，进入详情、复制番号并继续关联查询。
9. 右上角“导出”下载本次浏览器会话的数据、任务日志和模拟决定。

## 新增网站与创建采集任务

“来源管理 → 新增网站”可填写域名、资料字段与建议区域，点击“保存并创建任务”继续设置任务类型、站内目录路径和页数/记录上限。也可直接从 JavDB、Jable 或已登记网站的卡片创建任务。

网站计划显示“待接入采集器”，可以查看配置和取消。当前没有真实网站的执行器，不会发起网络采集；选择“本地合成样本”仍可运行原来的演示批次。详细说明见[网站与任务功能](../data-acquisition/WEBSITE_TASKS.md)。

审核列表提供状态筛选。网站与采集计划保存在当前浏览器，刷新保留；合成审核、样例运行和展示状态仍在内存中，刷新重置。换机器、浏览器或访问端口不会自动同步配置。导出 JSON 中的 `configuration` 可备份网站与计划，但当前没有导入界面，也不能直接作为正式发布或采集输入。本页没有真实账号认证或服务器持久化。

## 数据与测试

样本为 36 个作品、8 个虚构人物、4 个虚构厂牌；43 条输入中有 3 条同源重复、2 组冲突、2 条标题缺失。预置 24 条展示资料。全部来自本地生成器，**没有 JavDB / Jable 的真实作品、人物或图片**。

```bash
PYTHONPATH=workers/collector-python .venv/bin/python -m unittest discover -s workers/collector-python/tests
npm run test:collection-config
npm run test:collection-demo
```

浏览器测试使用已安装的 Playwright Chromium；如路径不同，设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指向本机浏览器可执行文件。不会自动安装浏览器或访问外部站点。

完整设计见[资料供给文档入口](../data-acquisition/README.md)，截图与实际测试结果见[本轮验证](../data-acquisition/VALIDATION.md)。
