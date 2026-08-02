# CHA CUP 营销图片工作台

这是一个面向电商营销任务的合规审查与确定性出图 MVP。它读取飞书 Base 任务，完成字段标准化、规则审查、DeepSeek 语义复核、模板化图片生成，并可将结果精确回写到原任务记录。

## 模式与管理员令牌

页面右上角默认显示“公开模式 ▾”。公开模式只能浏览演示任务、其处理记录、所用模板和已生成的结果图片。选择“进入管理员模式”后输入 `DEMO_ADMIN_TOKEN` 并点击“验证并进入”；管理员菜单会显示数据源管理、模板管理和退出入口。

令牌只保存在该页面的 JavaScript 内存中。它不会写入 localStorage、sessionStorage、Cookie、URL、日志或 HTML；刷新页面和退出管理员模式都会清空令牌及管理员专属状态。后端模板写接口仍会校验 `X-Demo-Admin-Token`，前端隐藏不是授权机制。

Vercel 项目设置中添加环境变量：

```text
DEMO_ADMIN_TOKEN=请使用独立的高强度随机值
```

本地 Vercel Python Function 使用链接项目的 Development 环境变量。先在 Vercel 项目设置中为 **Development** 配置 `DEMO_ADMIN_TOKEN`，再拉取该环境：

```bash
vercel env pull .vercel/.env.development.local --environment=development
vercel dev
```

根目录 `.env.local` 可供非 Vercel 本地工具使用，但不应被视为 `vercel dev` Python Function 的可靠注入来源；如果 `/api/sources` 返回 JSON `401` 与“执行口令无效”，请先检查 Development 作用域是否已配置该变量。Vercel CLI 的本地 Python builder 也不兼容在 `vercel.json` 中为 Python Function 写入 `maxDuration`，本项目因此不声明该元数据。

不要把 `.env.local`、`.vercel/.env.development.local` 或任何令牌提交到仓库。

## 模板管理 MVP

管理员从“模板管理”打开独立全屏视图：上传 PNG/JPG 背景，在画布上选择、拖动或缩放矩形区域，选择商品图片、品牌 Logo、主文案或活动价格类型，再保存草稿、测试生成和发布。模板 JSON 是内部实现细节，用户界面不提供 JSON 上传或编辑。

正式任务只会解析 `PUBLISHED` 模板；草稿仅可在管理员测试生成中使用。任务可选 `template_id`，支持“模板”“模板ID”“设计模板”和 `template_id` 列名；缺省时使用内置“经典展台”模板。Logo 只有在模板声明 Logo 区域时才需要。

模板使用版本化 [JSON Schema](.agents/skills/simple-visual-compliance/assets/templates/schema/template.schema.json)，并仅允许归一化坐标、四种绑定字段、contain/cover 和基础文字排版参数。发布前会校验背景、模板 ID、比例、区域边界、字段白名单及商品图/主文案/价格必需区。常见结构化错误包括 `TEMPLATE_NOT_FOUND`、`TEMPLATE_ASSET_MISSING`、`TEMPLATE_RATIO_NOT_SUPPORTED`、`TEMPLATE_LAYER_OUT_OF_BOUNDS`、`TEMPLATE_FIELD_NOT_ALLOWED` 和 `TEXT_OVERFLOW`。

## 存储与部署限制

内置演示模板随仓库发布，线上始终可读取。开发环境使用本地文件系统模板存储（可用 `TEMPLATE_STORAGE_DIR` 指定位置）。Vercel 运行时没有持久化模板存储适配器，因此模板写 API 会明确返回“当前部署未配置持久化模板存储”，不会显示虚假的保存成功。正式生产需要替换为持久化 `TemplateRepository` 适配器；本 MVP 不接入数据库或对象存储。

## 为什么没有 Canva MCP

第一版的重点是把设计背景、商品字段、合规审查、确定性生成和飞书交付连成可验证的闭环。Canva、Photoshop、Figma、稿定设计等任何设计工具都可以先导出 PNG/JPG 背景；Canva MCP 本身不能解决商品图、价格、文案和 Logo 的字段语义绑定，并会引入 OAuth、账号权限、套餐和外部 API 不确定性。未来可以在模板领域前增加 `DesignImporter` 接口，但当前未实现任何 Canva、Figma 或 PSD 集成。

## 测试

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest discover -s .agents/skills/simple-visual-compliance/tests -p "test_*.py" -v
```

本地离线流水线：

```bash
python main.py
```
