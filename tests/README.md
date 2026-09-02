# tests/ —— 质检单体系说明

> 这些测试是**对代码的承诺清单**（合同）：每个测试=一条"以后不许弄坏"的规矩。
> 代码改动跑一遍全量测试，红了就是有人违约。

## 目录结构

```
tests/
├── README.md            ← 本说明
├── conftest.py          ← 公共配置：让 pytest 找到 src/ 下的 file2kg 包
├── unit/                ← 单元测试：单个模块行为（只有这一层还需要入口/模拟）
│   ├── test_types.py    ← 数据契约：id/hash 格式、from_text、JobReport 账本
│   ├── test_loaders.py  ← 读取：md/txt 读出、GBK 容错、未知格式报错
│   └── (test_cleaner / test_chunker / test_embedder / test_store ...)
└── integration/         ← 集成测试：链路端到端（将来 test_ingest.py）
```

## 怎么跑

```powershell
D:\Miniconda3\envs\file2kg-env\python.exe -m pytest tests/ -v     # 全量
D:\Miniconda3\envs\file2kg-env\python.exe -m pytest tests/unit/test_cleaner.py   # 单文件
D:\Miniconda3\envs\file2kg-env\python.exe -m pytest tests/unit/test_cleaner.py -k 乱码   # 单用例
```

## 约定

1. **每个模块完成时，必须同餐附上质检单**（先写代码后补测试 = 测试会变摆设）
2. **测试红了，先改代码；只有"契约本身变了"才允许改测试**（比如 types 加字段，测试是照出新行为的）
3. **测试里不放真文件**：用 pytest 的 `tmp_path` 临时目录（真环境（如基准测试）放 scripts/ 下，见 bench_loaders.py）
4. **断言要"人话"**：`assert d.text == "今天学 RAG"` 一眼看懂，不写绕口令
5. 新增测试文件的命名：`test_<模块名>.py`，与 src 模块一一对应

> 性能基准（scripts/benchmark_loader.py）在 `scripts/` 不在 tests/——它是"体检"不是"质检单"。
> 记住区分：`loaders/`（产品代码）vs `benchmark_loader.py`（测速工具），名字现在终于不撞了。
