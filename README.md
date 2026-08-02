# CHA CUP 电商营销图片设计与风控合规系统

这是观猹 FDE 学习营结课项目。项目面向电商营销素材生产场景：把商品资料、营销任务、合规审查和图片交付串成可追溯的流程，减少人工在多张表、文案和设计稿之间反复核对的成本。

系统可从飞书 Base 读取商品与任务记录，并在首次接入时协助发现表结构、由用户确认业务字段映射。日常运行按 `source_id + task_id` 精确读取数据，完成字段标准化、价格/日期/画布/禁用词等确定性规则检查，再由 DeepSeek 进行语义复核。通过审查的任务使用确定性模板渲染商品图、主文案和活动价格，随后上传结果并将回执写回原任务；不通过的任务会保留结构化原因，避免继续出图。

页面提供公开只读模式和管理员模式。公开模式用于浏览演示任务及结果；管理员完成口令验证后，可接入数据源、处理待审查任务，并进入模板管理工作台。模板管理支持背景图上传、商品图/Logo/主文案/活动价格区域调整、草稿保存、指定已有任务测试生成和发布。正式任务只使用已发布模板。

主要实现位于 `src/`（业务语义、风控、渲染与接口编排）、`api/`（Vercel Python Functions）和 `index.html`（Dashboard）。模板定义与相关测试在 `.agents/skills/simple-visual-compliance/` 中。

## 本地启动

安装 Vercel CLI 后，在项目根目录运行：

```bash
npx vercel dev
```

管理员接口需要 `DEMO_ADMIN_TOKEN`。请在 Vercel 项目设置的 **Development** 环境配置该变量，再拉取本地开发环境；不要把令牌、`.env.local` 或 `.vercel/.env.development.local` 提交到仓库。Vercel 本地 Python Function 应以 Development scope 的环境变量为准。

```bash
vercel env pull .vercel/.env.development.local --environment=development
npx vercel dev
```

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest discover -s .agents/skills/simple-visual-compliance/tests -p "test_*.py" -v
```

## 部署说明

仓库内置模板可用于演示。开发环境可使用本地模板存储；当前 Vercel 部署没有持久化模板存储适配器，因此模板写入接口会明确提示该限制，而不会把临时写入当作已保存。若用于长期线上运营，需要接入持久化的模板与素材存储。
