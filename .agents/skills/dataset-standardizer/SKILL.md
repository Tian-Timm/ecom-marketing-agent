---
name: dataset-standardizer
description: 数据集规范化与清洗助手。用于将乱序、异名表头或含脏数据的 CSV/JSON 输入转换为统一的电商主图合成标准格式。
---

# Dataset Standardizer Skill

## 功能
1. 模糊匹配与自动对齐表头列名（如“宣传语”对齐为“主文案”，“折后价”对齐为“活动价”）。
2. 清洗数值格式（自动剥离“￥”、“元”等符号）。
3. 补全默认可缺省字段（如“画布比例”默认填充 `1:1`）。
4. 保留每条任务自己的活动名称、活动开始日期和活动结束日期，不使用全局日期覆盖。

## 与配置化飞书运行的边界

- CSV/JSON 文件导入仍采用宽松别名匹配和上述清洗/默认值策略，适合离线素材准备。
- 已激活的飞书数据源不使用本 Skill 的默认商品或默认关键字段：正式运行必须通过 `ACTIVE` `SourceConfig`，用 `source_id + task_id` 精确读取，并对商品/任务必填字段严格转换。
- 固定演示 Base 的 legacy 流程继续保留，不能将其字段假设扩散到配置化数据源。

## 命令行用法
```bash
python scripts/standardize.py --input raw_data.csv --output standardized_data.json
```
