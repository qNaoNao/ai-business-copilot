# 本地升级 2.0：你需要知道和手动完成的事项

项目仍在：`/Users/qixiaoma/Documents/Projects/ai-business-copilot`。
原数据库、.env、旧历史和原 ai-copilot Conda 环境没有被替换。
新依赖安装在项目自己的 `.venv`；运行新版本请用它。
不需要另装 Codex 插件。没有上传到 GitHub、开放公网访问或替你选择开源协议。

## 先打开免费演示

开发时本地页面已运行在 http://127.0.0.1:8501 。如果之后关闭了服务，在 VS Code 终端运行：

```bash
cd /Users/qixiaoma/Documents/Projects/ai-business-copilot
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

保持终端窗口运行，打开上面的网址。网址不是在线部署；关机后需要重新启动。
选择“免费演示（不调用 API）”，点击“品类→SKU下钻”，完成后点“沿用该SKU看周趋势”。
这个过程无需 API Key，也不扣模型余额；回答由预设流程生成，但数字与图表由本地数据库计算。

刷新或重新打开网页后：侧栏“可恢复工作流”选择会话，再点“恢复并继续该会话”。
该处可以继续提问；“历史会话”只读查看旧记录。未完成的付费会话恢复可能继续产生 API 费用。
“开始新会话”不会删除原历史；付费/免费切换也使用新的会话，避免混用结果。

## 修改知识库后

```bash
.venv/bin/python -m src.retrieval --build
```

构建本地向量索引，不调用付费 API。首次下载需要网络；后续检索可离线。
没建或索引过期时会显示 bm25-fallback。不要把这个降级模式写成向量检索成功。

## GitHub Desktop 提交步骤（你来做）

1. 打开 Desktop，Current Repository 选择 ai-business-copilot。
2. 在 Changes 检查本次变化，包括 workflow、retrieval、tests、docs、锁定依赖和 `.github/workflows/ci.yml`。
3. 确認列表没有 `.env`、`.venv`、`.cache`、`runs`、数据库、购买的 EchoMind 压缩包或付费源码。
   它们已被忽略，但仍应亲自看一次。不要把个人简历/联系人信息或 Key 带进去。
4. Summary 填 `Upgrade copilot with persistent workflow and local RAG`。
5. 点击 Commit to main，再点 Push origin。
6. 打开 GitHub 仓库的 Actions，查看 `API-free acceptance`。只有实际变绿后，才能说“CI 已通过”。
   这套工作流不需要配置 OpenAI Secret、不调用付费 API。
7. 若 Actions 报错，将日志发来处理。不要为了变绿删掉真正失败的断言。

## 需要你决定 / 登录的事情

- **发布**：我没有替你推送或公开仓库；你决定提交/分享的范围。
- **License**：还没有授权许可文件。是否允许他人使用和修改你的原创代码需你决定；
  商业购买资料不是自动可开源的代码，模型和数据条款也应分别查看。
- **在线 Demo**：当前只有本地服务。要发布，可先做禁用付费 API、使用合成数据的演示版；
  需要你选择托管平台、账户和是否公开。不要直接把含 Key 的本地服务暴露公网。
- **真实模型验收**：本次升级未消耗付费 API。若你希望再跑一次真实问答，
  选 OpenAI 模式、确认费用，输入一个限定范围的问题即可；无需把 Key 发到聊天里。
- **简历文件**：本次没有改 Word 简历。确认新版后再更新简历；下面文字只描述已实现能力。

## 可用于简历的准确表述（需自己能讲清）

AI Business Copilot｜本地电商经营分析 Agent（Python / SQL / Streamlit / LangGraph）

- 构建只读 SQL 经营工具与多轮工具调用链，支持月度指标、品类→SKU 下钻、周趋势及可追溯报告。
- 引入 LangGraph + SQLite 检查点实现节点级中断恢复，并结合本地多语言 BM25/向量混合检索输出带版本的知识引用。
- 建立模型调用预算、token 审计与证据边界规则检查；通过合成数据、模拟模型和网页测试完成 68 项本地自动验收。

不要写：已上线、多 Agent 系统、企业级安全、提升真实营收、真实用户量、模型准确率 91.7%。
91.7% 是这个小语料中 12 道相关留出检索题的 Hit@3，不是回答正确率。
GitHub Actions 尚未实际执行前，不写“CI 通过”；要在截图中保留免费模式的标识。

## 学习顺序（不需要今天全部会）

先运行两个免费示例，读工具输入/输出；再理解 SQL 指标与证据边界；
接着看 workflow 的五个节点和恢复测试；最后理解检索对照实验为何没有证明向量比 BM25 更好。
这比背框架名称更有助于解释自己的项目。
