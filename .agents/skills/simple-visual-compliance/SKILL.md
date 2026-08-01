---
name: simple-visual-compliance
description: 极简电商主图营销合规拦截与确定性图层合成 Skill。实现事前规则阻断、图层精确坐标合成与可视状态生成。
---

# Simple Visual Compliance Skill

## 职责边界

- LLM 按本 Skill 理解任务语义、解释违规原因并组织执行。
- `rules/forbidden_words.json` 是业务规则唯一来源。
- `audit_text.py` 负责价格、日期、画布、文案与外观修改等确定性红线。
- `assemble_image.py` 只接收 `PASSED` 任务，不维护业务规则。
- 前端不得自行判断 `PASSED` 或 `BLOCKED`。

## 输入

每条任务应包含：

- `task_id`：任务编号
- `img_type`：图片类型
- `aspect_ratio`：画布比例
- `deploy_date`：投放日期
- `campaign_start`、`campaign_end`：活动周期
- `promo_price`、`min_price`：活动价与最低允许价格
- `main_text`、`sub_text`：主文案与补充要求

CSV 可以使用对应的中文表头，数据规范化 Skill 会统一字段。

## 核心工作流

1. 从 CSV、JSON、固定演示飞书多维表格或已激活配置化飞书数据源读取任务；文件输入使用 `dataset-standardizer` 规范化。配置化飞书正式运行必须使用 `ACTIVE SourceConfig` 与 `source_id + task_id` 精确读取，严格转换商品和任务字段，不允许回退到默认商品或默认关键字段。
2. 读取唯一规则文件，运行 `audit_text.py`。
3. 确定性规则通过后，使用 DeepSeek 复核广告语义、产品外观修改要求和含混指令；模型不可用或结论不稳定时进入人工复核，不得默认放行。
4. 若状态为 `BLOCKED`，输出结构化违规项，禁止出图。
5. 若状态为 `PASSED`，运行 `assemble_image.py`。
6. 输出 `pipeline_result.json` 和通过任务的 PNG 图片；飞书模式会将图片上传云盘，并把状态、问题说明、图片附件和处理版本回写到独立输出字段。

## 运行方式

在项目根目录执行：

```bash
python .agents/skills/simple-visual-compliance/scripts/run_pipeline.py --input .agents/skills/simple-visual-compliance/assets/marketing_tasks.csv
```

启动带真实执行接口的本地工作台：

```bash
python .agents/skills/simple-visual-compliance/scripts/serve_app.py
```

浏览器打开 `http://127.0.0.1:8765`。

从固定演示飞书多维表格读取并完成交付：

```bash
python main.py --feishu
```

首次接入或调整字段后，先执行不上传、不回写的试运行：

```bash
python main.py --feishu --dry-run
```

飞书模式使用输入指纹避免重复回写和重复上传；只有任务输入变化或显式增加 `--force` 时才重新处理。

## 线上工作台

线上接口保留固定演示 Base 的 legacy 路径，同时支持管理员接入已确认的数据源：

- `GET /api/status`：查看当前数据源与能力状态。
- `GET /api/tasks`：读取飞书任务；未配置应用身份时返回仓库内演示快照。
- `POST /api/run`：受口令保护，仅处理用户选中的一条任务。
- `POST /api/sync`：受口令保护，每次最多处理 3 条待审查任务，不强制重跑历史任务。
- `GET /api/image`：代理读取已回写到 Base 的图片附件。

配置化数据源的首次接入为 `POST /api/discover` ➔ `POST /api/confirm`（保存 DRAFT 且执行只读 dry-run）➔ `POST /api/activate`；`GET /api/sources` 仅返回安全的活动源摘要。携带 `source_id` 的任务、运行和图片读取均需管理员口令。发现阶段只读，正式运行只通过 ACTIVE 配置及其回执精确回写。

线上运行需要在部署平台配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`DEEPSEEK_API_KEY` 和 `DEMO_ADMIN_TOKEN`。执行口令只保存在浏览器当前页面的内存中，不写入仓库或本地存储。

## 可复现评测

评测真值独立保存在 `assets/evaluation_ground_truth.json`，不进入业务输入和规则判断。

```bash
python .agents/skills/simple-visual-compliance/scripts/evaluate_fixture.py
```

评测报告输出到 `generated_output/evaluation_result.json`，包含任务状态一致率、异常检出率、规则码一致率和执行耗时。

## 输出约束

- `PASSED`：包含 `generated_image` 和图片元数据。
- `BLOCKED`：`generated_image` 必须为 `null`，并删除同任务编号的旧图片。
- 每条结果必须包含 `violations`、`rules_version` 和明确状态。
- 飞书输入字段与输出字段必须分离，不得把状态、问题说明、图片链接或图片附件写入“补充要求”。
