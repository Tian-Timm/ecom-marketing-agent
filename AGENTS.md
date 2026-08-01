# 项目指令与架构指南

1. **项目定位**：CHA CUP 电商产品营销图片设计与风控合规系统（对应仓库 `https://github.com/Tian-Timm/ecom-marketing-agent`）。
2. **核心流程**：首次接入由发现工具读取飞书 Base 的表、字段和少量样本 ➔ 用户确认业务映射并保存 DRAFT 配置 ➔ dry-run 通过后显式激活；正式运行使用 `source_id + task_id` 精确定位商品与任务 ➔ 严格标准化 ➔ 确定性风控规则（价格/日期/画布/禁用词） ➔ DeepSeek 语义复核 ➔ Python 图层确定性渲染 ➔ 飞书云盘上传与附件回写 ➔ 前端 Dashboard 实时展示。**发现工具负责首次接入；业务语义层负责日常稳定运行。**
3. **核心目录与文件职责**：
   - `index.html`：前端 Dashboard 交互界面（部署于 Vercel）。
   - `main.py` / `src/`：系统 Python 核心服务（风控引擎 `guardrail.py`、渲染引擎 `image_engine.py`、飞书接口 `feishu_closure.py`）。
   - `src/business_semantics/`：可配置数据源的领域模型、只读发现、用户确认与 dry-run、版本化 SourceConfig、结构漂移检查、`source_id + task_id` 精确读取和回执式回写编排边界。
   - `src/source_composition.py`：配置存储位置与受限 `credential_ref` 到部署环境凭据的解析；不得把密钥写入 SourceConfig。
   - `api/`：Vercel Serverless Server 端 API 接口路由。
   - `.agents/skills/`：内置规范化 Skill 与合规流水线测试套件。
   - `archive/`：存放过去的改进计划、阶段性任务需求与历史总结文档。
