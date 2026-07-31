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

1. 使用 `dataset-standardizer` 规范化 CSV 或 JSON。
2. 读取唯一规则文件，运行 `audit_text.py`。
3. 若状态为 `BLOCKED`，输出结构化违规项，禁止出图。
4. 若状态为 `PASSED`，运行 `assemble_image.py`。
5. 输出 `pipeline_result.json` 和通过任务的 PNG 图片。

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
