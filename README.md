# enterprise-pdf-rag

Python 3.12 / uv 财务 PDF RAG 后端。当前可运行的业务入口是**单份真实 AIA 2026 中期业绩演示报告的来源 ingestion 与审阅**：保存原始 PDF、全部 71 页的原生 SVG 和文本位置，提供逐页浏览与第 25 页股息图选区。

这是 AIA Group 报告。ChartIR、LLM 图表描述、文字与 SVG 元素的对应关系、视觉完整性仍为 **pending**；当前界面不提供已验证的财务问答，也未运行真实 embedding/rerank。`text.json` 是 pdfspine 原文观测，不能当作 LLM 描述或 embedding 输入。

## 处理选定文件

所有命令从仓库根目录运行：

```sh
uv sync --locked --extra pdf
uv run --locked enterprise-pdf-rag ingest-aia
```

输入固定为 `data/samples/aia-group-2026-interim-results-presentation.pdf`，SHA-256 必须为 `df902346791b300566761bfcd42bc93bf19e7ba86273dd0cf32d2bb7e9f0870e`。PDF 不随公共仓库分发；下载位置与来源见 [样本说明](docs/samples/aia-report.md)，[71 页盘点](docs/samples/aia-2026-interim-inventory.md) 和 [基准 manifest](benchmarks/aia-2026-interim/manifest.json)。不匹配的文件在解析前拒绝；缺页或失败有明确诊断，不静默遗漏。

全部输出在 `data/output/aia-2026-interim/`，不使用项目根的 `output/`：

| 路径 | 内容 |
| --- | --- |
| `source.pdf` | 已校验并保存的原始文件 bytes |
| `review.html` | 71 页导航与第 25 页重点选区 |
| `pages/page-001.html` … `page-071.html` | 每页原生 SVG、原文及 bbox、前后页导航 |
| `text.json` | 全 71 页 pdfspine 原文观测，含源 SHA、物理页号与文本位置 |
| `chart-ir.status.json` / `description.status.json` | 尚未生成的语义产物状态；`pending`、`artifact_id: null` |
| `objects/sha256/<digest>` | 不可变、内容寻址的 PDF/SVG/text sidecar/manifest |
| `current-manifest` | 本地当前完整 manifest 的标识 |
| `attempts/*.json` | 单独保存运行时间、成功/失败与诊断，不参与来源身份 |

对象读取会校验摘要和长度；同址内容冲突、缺失和跨页引用均拒绝。页级和选区侧车绑定同一源文件。选区保留原生 SVG 子树不等于证明视觉等价：第 25 页 SVG 的底部红色基线比 PDF 渲染细，详情在审阅页和 manifest 的诊断中。

## 本地 Open WebUI

本机已有 Open WebUI **0.6.5** 时，可启动隔离兼容预览：

```sh
uv run --locked python scripts/webui_preview.py start
uv run --locked python scripts/webui_preview.py status
# 停止本项目的两个进程，保留数据
uv run --locked python scripts/webui_preview.py stop
```

打开 [Open WebUI](http://127.0.0.1:8767)，选择 `AIA 2026 中期业绩 — 原文审阅 / 语义待验证`，输入 `查看当前文件` 或 `查看第25页`。API 在 `127.0.0.1:8766`；[来源浏览](http://127.0.0.1:8766/v1/aia/review) 提供原始资产与逐页入口。回答固定到已保存的 manifest，不调用模型、不使用合成数值回退。

Open WebUI 的内置上传、PDF 解析、RAG、工具和后台自动生成被部署边界阻断。厂商进程使用私有数据库、静态资产、缓存和最小环境，拿不到上游 API key。默认业务 profile 是 `aia-source-review`。启动前需完成上述真实文件 ingestion；缺源资产会失败，不会自动切 demo。

隔离容器固定官方 **0.11.3-slim** 镜像摘要；本机无可用 Docker daemon，容器尚未实际运行。启动方式、版本区别、限制和已知旧版首次启动静态资产副作用见 [Open WebUI 使用说明](docs/open-webui.md)。不安装或修改全局依赖。

## 来源 API

- `GET /v1/aia/manifest`：固定来源与完整资产清单。
- `GET /v1/aia/pages/25/text`：第 25 页原文和 bbox。
- `GET /v1/aia/assets/<digest>`：只读取属于当前 manifest 的已验证对象。
- `GET /v1/aia/review`：逐页来源审阅。
- `GET /v1/aia/pages/page-001.html`：单页 SVG 与原文；页面号支持 001–071。
- `GET /v1/aia/source.pdf`、`GET /v1/aia/text.json`：同一来源的完整 PDF 和原文侧车导出。
- `GET /v1/models`、`POST /v1/chat/completions`：受限原文审阅，支持非流式与 SSE。

未知模型、越界页、错 snapshot、缺源证据或财务推断请求均明确拒绝。已有持久源资产、不可变 manifest 和本地原子指针；生产级多存储 CAS 发布、ACL/撤回、并发调度与完整财务 QA 尚未实现。

## 显式合成回归示例

下面的 `Revenue 2024=10 / 2025=15` 是内部创作的 PDF fixture，**不是 AIA 数据**，不作为默认业务入口：

```sh
uv run --locked enterprise-pdf-rag demo --mode offline-demo --output data/output/demo
# 仅在明确需要检查合成UI回归时使用；需先停止当前预览
uv run --locked python scripts/webui_preview.py start --profile offline-demo
```

这个独立测试链覆盖同 SVG 的 ChartIR/description 配对、仅 description 的 demo token-hash embedding、固定 snapshot 回填和字段证据。真实 embedding/ChartIR 描述接线尚未完成；hash 向量不代表语义检索。保守的单页提取命令仍保留：

```sh
uv run --locked enterprise-pdf-rag extract \
  --pdf data/samples/aia-group-2026-interim-results-presentation.pdf \
  --page 10 --bbox 30 120 310 330 --output data/output/aia-page-10
```

## 开发与验收

```sh
uv run --locked pytest tests/documents  # TDD 先跑相关测试
make fmt                              # 本地安全修复与格式化，会写文件
./ci.sh                               # 唯一完整、只读、离线工程门
```

重大改动后和阶段收尾必须运行完整 `./ci.sh`：Ruff、strict mypy、纯领域架构、版本化 schema、文档漂移、单元及离线集成测试，warnings 当作错误。测试不连接网络；真实语料测试在本地样本存在时执行，公共仓库不包含 PDF。协议与失败契约仍有独立小型离线 fixtures。

真实 LLM 测试只在大版本或模型调用流程实质变化时显式触发，普通改动使用 transport 替身。本轮来源 ingestion 不需要 LLM。已有 `llm-smoke` 仅验证连接；从环境读取 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，不读取 `.env`、不打印密钥，也不证明图表质量。本地 embedding/rerank 使用独立配置，未配置时拒绝，不继承云端 LLM。

架构和范围见 [ADR 0001](docs/adr/0001-architecture.md)、[图表链 ADR 0002](docs/adr/0002-figure-pipeline.md)、[UI ADR 0003](docs/adr/0003-open-webui.md)、[真实来源 ADR 0004](docs/adr/0004-aia-source-review.md)、[PRD v0.2](docs/PRD-v0.2.md)。PDF、密钥、运行产物、虚拟环境与本地 IDE 配置不进入公共仓库。
