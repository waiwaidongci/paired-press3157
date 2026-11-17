# 验证记录

本文件记录实际执行过的检查。运行环境：Linux，Python 3.11.2，NumPy 2.2.1，SciPy 1.15.1，pytest 8.3.4，SQLite 3.40.1（本地测试）；生产 compose 使用 PostgreSQL 16。仓库内的 `docker` 命令在本环境不可用，因此未运行容器端到端；下文明确区分“已执行”和“未执行”。

## 1. 后端单元与接口测试（已执行）

```bash
cd backend
python3 -m pytest tests -q
# 40 passed
```

| 文件 | 覆盖内容 |
|---|---|
| `tests/test_sequential.py` | 逐样本递推与 Beta 闭式解一致；首对预测概率 1/2（增量为 0）；第二对正号增量 log(1.5)；零差值记为结且不产生证据；交换 before/after 符号取反但证据不变；仅 M≥20 判停；极端长序列的展示值 JSON 安全；四种零假设模拟（逐对查看、离散含结、随机停看间隔、不同种子）下全程误报率均 ≤ 5% 抽样置信上界；均值偏移下功效明显高于零假设。 |
| `tests/test_monitor.py` | 试验启动锁定 α=0.05、门槛 20，同一批次只允许一个未封存试验；每次查看只纳入新增完整纸样并跳过不完整记录；重放同一查看、重复 request_key、两个查看并发执行均不重复纳入；乐观版本冲突后从最新状态重算并完成；停止判定冻结纳入范围；已纳入纸样数值修改、改名、删除都会封存且旧证据与冻结值不变；相同数值保存不封存；新增纸样不封存；已纳入纸样证据等于纳入符号序列的逐样本重放、增量之和与闭式解；中断为 running 的查看可恢复；晚到执行不能倒退证据；晚到执行不能解除封存；绕过服务层的直接改动会被下一次查看检测到并封存；手动封存后可启动新试验且旧证据保留。 |
| `tests/test_api.py` | HTTP 角色（viewer 不能启动试验但可读取）、启动试验返回锁定参数、查看流程、重复 request_key 幂等、编辑已纳入纸样返回封存信息且试验页反映封存、未知试验 404、原固定终点运行结果接口仍可读。 |

## 2. 逐样本计算核对（已执行）

- 逐样本 `log_increment` 累加 = 最终 `log_e` = 纳入符号序列的 `replay` 结果 = 闭式解
  `nz·log2 + log B(k+½, nz−k+½) − log B(½,½)`（`tests/test_monitor.py::test_evidence_is_the_replay_of_the_included_sign_sequence`，误差 < 1e-9）。
- 独立蒙特卡洛（20000 次、最多 300 对、逐对查看）：全程误报率 0.0375，低于 0.05 的 Ville 上界。
- 7 个连续同号差值的 M = 26.8125，第 7 对越过 20；第 6 对为 10.9375，未越界。

## 3. 前端渲染核对（已执行，jsdom）

用真实后端输出作为 fixture，在 jsdom 中执行 `overview.render()`、`analysis.render()`、`results.render('trial-1')`、`results.render()`：

- 四个页面 HTML 均不含 `undefined`/`NaN`。
- 试验结果页包含“累计证据、停止判定与纳入范围”表格、纳入序号区间、不完整记录数量、停止后不再纳入数量、查看记录表。
- 分析页显示锁定 α=0.05 与方法依据；启动/查看/封存按钮存在且查看按钮会先 POST `/monitor/trials/{id}/looks`。
- 停止后的试验页不显示新增查看按钮。
- 所有 ES 模块通过 `vm.SourceTextModule` 解析。

## 4. 未执行的验证

- 未运行 `docker compose up`：本环境没有 docker CLI。因此未在 PostgreSQL/Redis 实际容器中验证锁语句（`SELECT … FOR UPDATE`、条件 `UPDATE`）与 worker 心跳。
- 未在真实浏览器中点击（无浏览器）；前端只做 jsdom 渲染与事件绑定检查。
- SQLite 下 `FOR UPDATE` 被忽略，并发正确性由唯一约束与乐观版本条件保证；PostgreSQL 下还会额外持有行锁，顺序固定为“试验 → 观测”，编辑与查看不会死锁。

## 5. 复现

```bash
cd backend
DATABASE_URL=sqlite:////tmp/t.db REDIS_URL= JWT_SECRET=test python3 -m pytest tests -q
```
