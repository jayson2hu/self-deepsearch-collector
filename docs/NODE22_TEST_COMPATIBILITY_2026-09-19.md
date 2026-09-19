# Node 22 捕获测试入口兼容

需求来源：双项目联调剩余项续作。此前 `test:jable-capture` 使用当前 Node 22 不支持的 `--test-isolation=none`，导致用例尚未执行就退出。

修正：直接执行使用 `node:test` 的测试文件，保留测试断言和退出码，不禁用测试、不升级运行时、不重新采集数据。

验收命令：`node --version`、`npm run test:jable-capture`。预期三个捕获测试均通过，退出码 0。实际结果见本次提交前的执行记录；原 SQLite、真实归档和头像不修改。该入口不连接外部采集站点。
