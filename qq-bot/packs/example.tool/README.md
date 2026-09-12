# example.tool —— 工具包示例（type=tool）

**这个包默认什么都不会做。** 这是刻意的：`core/packs.py` 的 `load_tools()` 只有在
env `PACKS_ENABLE_PY` 显式开启时才 import 包内代码；没开的时候**连 import 都不做**
（零执行、零子进程）。包本身照常被 `discover()` 发现——"装着却不动"就是它的演示内容。

## 抄它之前先问一句

九成的 mod 需求用**数据包**（card / voice / item / emotion / world / gal）就能满足，
它们零代码、零风险、不用开任何开关。只有需要**真·代码能力**（联网取数、复杂计算）时才做工具包。

## 文件

| 文件 | 作用 |
|---|---|
| `pack.json` | 清单：`type=tool` + `tools[].entry`（包内相对路径）+ `permissions` + `limits` |
| `local_time.py` | entry：模块顶层按 ToolCard 契约自注册（`agent/registry.py`），无其它副作用 |

## 契约在哪

- `docs/接口文档.md` §3.7 —— `tools` / `permissions` / `limits` 字段与"默认不加载"
- `docs/接口文档.md` §6.3 —— 权限词表（`fs.read:` / `fs.write:` / `net:` / `llm` / `env:`）与沙箱边界
- `docs/接口文档.md` §6.1 —— ToolCard 契约（`register` 的两种形态 / `failure` / `needs_llm`）
- `docs/MOD开发指南.md` §7 —— 从零做工具包的步骤与三条硬约束

## 三条硬约束（照抄时会踩的）

1. **默认不加载**：`PACKS_ENABLE_PY` 默认关。给普通用户的包**不要依赖它**。
2. **必须声明权限**：缺省 `permissions: []` = **零能力**。本包只读了环境变量 `TZ`，
   所以只声明 `env:TZ`——声明要贴着代码实际用到的能力写，多写就是多要。
3. **不能指望主进程环境**：沙箱落地后工具代码跑在独立子进程里，拿不到 `.env`、
   拿不到 API Key、拿不到主进程内存（`docs/S4-沙盒与声明式权限-spec-2026-09-12.md`）。

## 自己试一下

```bash
cd qq-bot
.venv/Scripts/python.exe -c "from core import packs; print([p.name for p in packs.by_type('tool')], packs.load_tools())"
# → ['example.tool'] []        ← env 没开：被发现，但没有装载
```

想真的装载（**只在信任这个包时做**）：

```bash
set PACKS_ENABLE_PY=1     # PowerShell: $env:PACKS_ENABLE_PY="1"
.venv/Scripts/python.exe -c "from core import packs; print(packs.load_tools())"
```
