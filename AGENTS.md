# 项目指令与架构指南

1. **项目定位**：CHA CUP 电商产品营销图片设计与风控合规系统（对应仓库 `https://github.com/Tian-Timm/ecom-marketing-agent`）。
2. **核心流程**：飞书 Base 任务拉取 ➔ 数据规范化 ➔ 确定性风控规则（价格/日期/画布/禁用词） ➔ DeepSeek 语义复核 ➔ Python 图层确定性渲染 ➔ 飞书云盘上传与附件回写 ➔ 前端 Dashboard 实时展示。
3. **核心目录与文件职责**：
   - `index.html`：前端 Dashboard 交互界面（部署于 Vercel）。
   - `main.py` / `src/`：系统 Python 核心服务（风控引擎 `guardrail.py`、渲染引擎 `image_engine.py`、飞书接口 `feishu_closure.py`）。
   - `api/`：Vercel Serverless Server 端 API 接口路由。
   - `.agents/skills/`：内置规范化 Skill 与合规流水线测试套件。
   - `archive/`：存放过去的改进计划、阶段性任务需求与历史总结文档。