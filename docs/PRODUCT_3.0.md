# 受控经营分析工作台：设计、运行与验收

状态日期：2026-10-17。本轮扩展保留原有对话、LangGraph 检查点、混合检索、品类→SKU 下钻、历史与报告能力；新增可组合指标查询、分析资产库和独立 HTTP 接口。应用仍是本地、单工作区系统，不是已经上线的商业 SaaS。

## 1. 对标范围与取舍

参考成熟项目的设计方向；本次实现未复制第三方源码。第三方许可证与现有项目的技术出处保持独立。

| 参考项目 | 官方资料中的能力 | 本项目采用的设计方向 | 没有实现或不作出的承诺 |
|---|---|---|---|
| [WrenAI](https://github.com/Canner/WrenAI) | 受治理的语义层、指标定义、查询验证与结果展示 | 版本化指标目录，结构化查询意图编译，SQL/参数/口径可核查 | 不是其完整引擎，不支持任意 SQL 或多数据库连接器 |
| [Vanna](https://github.com/vanna-ai/vanna) | 工具权限、会话存储、可观测性与生命周期扩展 | 独立只读查询 API、两种静态令牌角色、本地运行审计 | 没有企业身份系统、用户级权限或租户隔离 |
| [Dify](https://github.com/langgenius/dify) | 工作流、知识库、日志监控与 API 集成 | 在现有工作流之外补齐可复用分析、反馈记录与服务接口 | 没有复制其可视化编排平台，也不声称具有相同生产能力 |

这些项目规模和定位不同，未以星数或功能数量给本项目排名。商业成熟度还需要真实用户、持续运行、负载、安全与运营数据验证。

## 2. 从固定问答走向可组合查询

新增第7个工具 `query_business_metrics`。模型只选择指标、维度、日期与筛选条件；SQL 标识符、连接和计算表达式由本地目录产生，不接受模型或用户提供的 SQL。

共享执行链路：结构化意图 → 白名单校验 → 参数化 SQL → 来源粒度检查 → 有界只读执行 → 证据包。工作台、模型工具和 HTTP API 使用同一引擎，避免三处维护不同口径。

- 9个指标：GMV、订单、客户、件数、运费、AOV、平均商品价格、平均每单件数、运费占总支付比例。
- 5个维度：日、周、月、品类、product_id；单次最多2个维度，最多1个时间维度。
- 日期范围包含首尾两天，最多1096天；最多返回500行，额外取1行检测截断。
- 仅统计已交付订单，按订单购买时间筛选。金额币种未注明。
- 商品明细粒度为 `(order_id, order_item_id)`；连接前校验订单、商品、客户键及商品明细键唯一且非空，防止连接膨胀。
- 客户数、订单数不可以跨品类或SKU相加；比率不可简单平均。品类AOV是该品类商品金额/含该品类商品的订单数。
- 本引擎不补零，不自动计算日均。首末周或月可能不完整。日均贡献排名继续使用原有专用工具。
- 空分组返回空列表；无维度聚合的空区间返回0总量、null比率。它们不是缺货或商品下架的证据。

每份证据包包含查询条件、SQL、绑定参数、EXPLAIN 查询计划、结果与截断标志、指标定义、口径版本和来源版本。

来源版本是文件路径/大小/修改时间及存在时的WAL元数据的哈希指纹，**不是数据库内容哈希**；查询期间元数据变化会拒绝结果。不能把这项检查包装成不可变数据仓库或完整血缘平台。

## 3. 执行与数据安全

除了结构化白名单和参数绑定，执行连接还启用 SQLite `mode=ro`、`query_only`、列/函数 authorizer、最多2000万条虚拟机指令、5秒执行预算与输出行数限制。预算包括来源粒度检查；查询过大时应缩小时间或维度，不是解除限制。

这些控制针对本项目的受控查询，不是通用恶意SQL沙箱。查询限制不是进程级内存隔离，行数上限也不意味着底层只扫描这些行。

项目配置关闭表格组件原生CSV导出与剪贴板复制，统一使用应用的CSV导出按钮，对可被电子表格当作公式的文本加保护前缀，数值保持原值。JSON证据和CSV可能仍含业务数据，下载后需要按数据政策保管。

## 4. 免费工作台操作

1. 按项目README启动Streamlit，侧栏选择 **工作区 → 数据探索与分析资产**。
2. 默认日期为2018-01-01至2018-02-28；默认按月份、品类查看GMV、订单、客户和AOV。
3. 点击 **预览查询**：只编译和展示SQL，不执行数据查询。
4. 点击 **执行受控查询**：运行本地只读SQL，显示表格、图表、口径与版本。
5. 输入分析名称，点击 **保存分析快照**。
6. 分析资产库可以 **查看快照（不重新查询）**，或 **用当前数据重新执行**。重跑显示来源、口径或结果是否变化，不会覆盖原快照。
7. 记录 `needs_review` / `verified` 和备注。它是人工复核意见，不是模型准确率。

分析资产、反馈与本地审计存在 `runs/analyses.sqlite3`，可用 `COPILOT_ANALYSIS_STORE` 改变位置。保存/复核与相应审计事件在同一事务中提交。快照内容摘要用于发现意外损坏，不防具有文件写权限的攻击者。无自动删除原快照的操作。

## 5. 独立 HTTP API

使用已验证的Python3.11环境与依赖快照。API没有付费模型入口，不自动读取`.env`中的密钥。

```bash
export BUSINESS_DB_PATH="$PWD/.demo/business.db"
export COPILOT_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m uvicorn src.api:create_app --factory --host 127.0.0.1 --port 8000
```

首次运行且`.demo/business.db`不存在时，先执行 `python -m src.demo_data`；该命令拒绝覆盖已有数据库。API令牌是本服务的访问口令，**不是OpenAI API Key**，不要提交到Git，也不要截图分享。

| 路由 | 作用 | 角色 |
|---|---|---|
| `GET /healthz` | 进程存活检查，不代表数据或模型可用 | 无需令牌 |
| `GET /v1/catalog` | 指标目录、口径与限制 | viewer / analyst |
| `POST /v1/query/preview` | 编译查询，不执行SQL | viewer / analyst |
| `POST /v1/query` | 执行受控查询 | viewer / analyst |
| `GET /v1/analyses` | 最近50条分析摘要 | viewer / analyst |
| `GET /v1/analyses/{id}` | 原快照与反馈 | viewer / analyst |
| `POST /v1/analyses` | 服务器执行查询后保存，不接受客户端伪造结果 | analyst |
| `POST /v1/analyses/{id}/replay` | 用当前数据重跑，body为`{}` | analyst |
| `POST /v1/analyses/{id}/feedback` | 追加复核意见 | analyst |
| `GET /v1/audit` | 本地查询成功/失败计数、均耗时、最近事件 | analyst |

所有`/v1`请求需要 `Authorization: Bearer <token>`。`COPILOT_API_TOKEN`对应analyst；可选的`COPILOT_VIEWER_TOKEN`对应viewer，两个令牌必须不同且至少32个ASCII字符。未配置强令牌时API拒绝启动。

viewer可以读取本工作区全部保存结果和执行只读查询，不能保存、复核或读取审计。**这不是不同客户间的数据隔离**。本地Streamlit工作台不受这两个HTTP令牌保护，不能把它当作已鉴权网页。

默认每种角色每分钟60次请求、每进程最多4个同时处理请求，JSON请求体最多16KB。超限返回429/503，查询执行超时返回408，非法输入400，认证失败401，权限不足403。限流在内存中，重启会重置，不能跨多个进程协调。错误不返回数据库路径或堆栈。

查询body示例：

```json
{
  "metrics": ["gmv", "orders", "customers", "aov"],
  "dimensions": ["month", "category"],
  "start_date": "2018-01-01",
  "end_date": "2018-02-28",
  "limit": 100
}
```

保存body为`{"title":"月度品类分析","query":<上述对象>}`，反馈body为`{"verdict":"needs_review","note":"核对去重口径"}`。HTTP接口采用标准JSON封装：`request_id`与`data`，或`request_id`与`error`。

## 6. 容器化交付

```bash
docker compose up --build
```

Compose仅映射本机`127.0.0.1:8501`。镜像以非root用户运行、使用合成演示数据库、默认BM25与免费模式；持久化分析/检查点保存在独立volume。`.env`、真实数据库、历史记录、购买资料和模型缓存不进入构建上下文。没有自动把本机OpenAI密钥注入镜像的步骤。

截至本次本地验收：`docker compose config --quiet`通过，但Docker引擎未启动，因此**镜像构建及容器运行尚未在本机验证**。新增CI任务会构建镜像并检查容器内合成SQL结果，必须以新提交实际Actions结果为准。容器配置不等于公网部署。

公网分享仍需另行完成HTTPS、网页鉴权、数据隔离、限额、密钥管理和运行监控。付费模式不得直接无鉴权开放。

## 7. 本次验收记录与边界

本地完整套件：**105项测试通过**，包含原68项及新增37项；命令如下：

```bash
COPILOT_RETRIEVAL=bm25 LANGSMITH_TRACING=false python -B -m src.acceptance
python -m pip check
docker compose config --quiet
```

机器记录默认输出到`runs/acceptance.json`；CI上传该记录。无私有数据库的全新检出会明确跳过原11项私有数据检查，不把跳过项算作通过。新增测试均使用临时合成数据库，不需要密钥。

覆盖SQL已知值、9个指标与旧工具一致、跨品类订单不可加、首尾日期、周标签、空数据、截断、参数绑定、非法意图、写入/ATTACH/元数据/扩展访问拒绝、执行预算、重复商品明细拒绝、图表工作台、快照重启与回放、摘要校验、并发保存、令牌、角色、请求大小、限流和错误脱敏。另以模拟模型响应验证新工具能通过原有LangGraph循环调用，不代表新一轮真实模型问答验收。

在本机现有经营数据库上，2018年1–2月的新查询与旧月度工具的GMV、订单、客户、件数、运费和AOV已逐项一致。私有数据和明细未写入本报告。

未完成：新的云端CI、容器运行、公网Demo、真实模型评测、大规模负载测试、企业SSO、多租户和生产SLA。因此可以展示可运行的受控分析应用及工程实现，但不能声称与成熟商业产品等同或已在企业生产使用。

## 8. 技术参考

- [Python sqlite3：只读连接、authorizer与进度中断](https://docs.python.org/3.11/library/sqlite3.html)
- [Starlette 路由](https://starlette.dev/routing/)与[TestClient](https://starlette.dev/testclient/)
- [Streamlit 应用测试](https://docs.streamlit.io/develop/api-reference/app-testing)
- [Docker 构建最佳实践](https://docs.docker.com/build/building/best-practices/)

上表第三方项目仅作为设计参考；现有依赖与数据的许可须分别遵守，未替原始项目选择或宣称新的开源许可证。
