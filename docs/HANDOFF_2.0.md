# 运行与复现指南

受控分析工作台、分析资产库和独立HTTP接口的新增操作见[工作台与接口指南](PRODUCT_3.0.md)。

本文说明环境配置、免费演示、会话恢复、检索索引和离线验收。
应用目前在本机运行，公开仓库不等于公开托管的在线服务。

## 环境配置

使用 Python 3.11，在仓库根目录创建并激活环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock.txt
```

以下命令面向 macOS/Linux。依赖版本由 `requirements.lock.txt` 固定。

## 数据与免费演示

私有电商数据库不随仓库分发。全新检出可使用合成数据：

```bash
python -m src.demo_data
export BUSINESS_DB_PATH="$PWD/.demo/business.db"
python -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

合成数据默认生成在 `.demo/business.db`，不代表真实业务经营结果。
生成器拒绝覆盖已有数据库；数据库已存在时不必重复执行生成命令。
网页在没有私有数据库时也可自动使用合成演示数据。

打开 `http://127.0.0.1:8501`，保持启动服务的终端运行。
选择“免费演示（不调用 API）”，依次执行“品类→SKU下钻”和“沿用该SKU看周趋势”。

免费演示使用预设分析流程执行真实 SQL，展示查询结果、图表、引用和审计信息；
不调用大模型，也不提供任意自然语言问题的模型推理能力。

## 会话与中断恢复

侧栏“可恢复工作流”可选择已保存会话，再点击“恢复并继续该会话”。
节点完成后写入 SQLite 检查点，恢复时可复用已保存的查询证据和所选品类/SKU。
“历史会话”用于只读回溯原有 JSONL 记录。

“开始新会话”不会删除旧历史。免费和付费模式使用独立会话。
恢复未完成的付费工作流可能继续请求模型 API 并产生费用。
检查点提供节点边界恢复，不保证远程 API 计费恰好一次。

## 本地知识检索

可显式构建本地向量索引：

```bash
python -m src.retrieval --build
```

首次构建需要联网下载多语言嵌入模型；嵌入计算不调用付费 API。
知识内容变化后需要重新构建。索引缺失、过期或模型不可用时，
检索会显示 `bm25-fallback`，不会将降级结果标为向量检索。

## 可选模型模式

复制 `.env.example` 为 `.env`，在本机配置 API Key 和模型名称，
然后在页面明确选择 OpenAI 付费模式。`.env` 不应提交到 Git。

付费模式会将问题、检索片段和工具结果发送至模型 API，
并通过 `store=True` 和 `previous_response_id` 维护响应链。
使用非公开数据前应核对相应的数据与存储政策。

请求和工具调用有数量预算，实际 Token 用量可在审计区查看。
费用估算需要配置普通输入、缓存输入和输出单价；未配置时费用显示为未知。

## 测试与检索评测

自动测试无需模型 API Key：

```bash
COPILOT_RETRIEVAL=bm25 python -B -m unittest discover -s tests -v
python -m src.retrieval_evals
```

原电商数据库的确定性经营验收另行执行：

```bash
python -m src.offline_evals
```

该经营验收依赖未分发的原数据库，不能将合成数据结果冒充原数据验收。
完整单元测试会明确跳过缺少私有数据时无法执行的专用检查。

向量索引已构建后，可对比本地混合检索：

```bash
python -m src.retrieval_evals --dense
```

GitHub Actions 的 `API-free acceptance` 运行依赖安装与兼容性检查、
合成 SQL/恢复/网页验收，以及关键词和 BM25 检索评测。
CI 不调用付费 API，也不下载向量模型。
提交 `01e58f8` 在 2026-10-06 的首次云端运行已通过；
后续提交的结果以 [Actions 运行记录](https://github.com/qNaoNao/ai-business-copilot/actions/workflows/ci.yml) 为准。

检索实验的语料规模、指标定义和局限详见
[工程设计与验收](ENGINEERING_UPGRADE.md) 与 [评测说明](evaluation/README.md)。

## 文件与部署边界

凭据、数据库、缓存和运行历史不随源码分发。
报告可包含问题、回答和查询证据，对外分享前应检查是否存在非公开数据。

当前服务没有公开在线 Demo、认证、租户隔离或多用户托管能力，
不能直接将带有付费凭据的本地服务暴露公网。
代码、模型和数据的使用条件应分别核对；仓库尚未提供 License 文件。
