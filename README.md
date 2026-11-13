# registry_delta_hub

多来源登记增量处理：身份键、来源修订、质量门禁、人口汇总和发布记录。当前入口是 `registry_delta_hub.console.run`，它使用本地 JSON 资料和 SQLite 状态；目录 `registry_delta_hub` 存放业务模块，`tests/registry_batches` 验证既有业务行为。

## 环境

使用 Python 3.11，全部依赖来自标准库。`requirements.txt` 为空，不需要下载数据、模型或第三方包。数据库在运行时创建，可以删除临时数据库重新开始。

## 检查

```bash
python3 -m unittest discover -s tests/registry_batches -v
python3 -m unittest discover -s tests/analysis_context -v
python3 -m compileall -q registry_delta_hub tests scripts
```

## 运行

```bash
python3 -m registry_delta_hub.console.run  fixtures/demo.json --db /tmp/registry_delta_hub-demo.sqlite3
python3 scripts/check_project.py
```

`check_project.py` 串行执行测试、编译和真实业务入口，将本次业务输出完整打印到终端；状态文件使用独立临时目录，重复运行不沿用上次状态。

批量分析资料位于 `fixtures/rate_bridge.json`，字段含义见 `docs/data.md`。它与当前单笔流程的输入格式不同，不能直接交给原来的入口处理。
