# 模块 02：Vercel 生产环境与实时读取

## 前置条件

只有模块 01 全部通过后才能执行本模块。

需要具备：

- 可用的 `FEISHU_APP_ID`
- 可用的 `FEISHU_APP_SECRET`
- 已存在的 `DEEPSEEK_API_KEY`
- Vercel 项目 `tian-timms-projects/cha-cup-marketing-demo`

## 任务目标

把飞书应用身份安全配置到现有 Vercel 项目，使原生产链接从演示快照切换为实时读取固定飞书 Base。

本模块先只验证读取；写入能力只有在管理员执行口令配置后才启用。

## 安全边界

- 所有敏感变量必须使用 Vercel Sensitive 类型。
- 禁止把变量值写入 `vercel.json`、`.env.example`、Markdown、Git 提交或终端总结。
- 前端不得获得 `FEISHU_APP_SECRET` 或 `DEEPSEEK_API_KEY`。
- `DEMO_ADMIN_TOKEN` 至少使用 32 字节随机值。
- 管理员口令只允许保存在 Vercel 环境变量和用户明确选择的本地安全位置。
- 不得将管理员口令写入浏览器本地存储。

## 环境变量

生产环境需要：

```text
FEISHU_APP_ID
FEISHU_APP_SECRET
DEEPSEEK_API_KEY
DEMO_ADMIN_TOKEN
```

其中：

- `DEEPSEEK_API_KEY` 已经配置，先检查名称存在，不读取明文。
- `FEISHU_APP_ID` 为 `cli_aae2a8cf21239be2`。
- `FEISHU_APP_SECRET` 来自模块 01。
- `DEMO_ADMIN_TOKEN` 新生成，不复用飞书或 DeepSeek 密钥。

## 执行步骤

1. 确认当前 Vercel 登录身份：

```powershell
npx.cmd --yes vercel whoami
```

预期为 `tian-timm`。

2. 确认项目绑定：

```powershell
npx.cmd --yes vercel pull --yes --environment production
```

3. 只列出环境变量名称，禁止输出变量值。
4. 通过标准输入或 Vercel 安全交互添加三个缺失变量。
5. 重新拉取生产配置并构建：

```powershell
$env:UV_CACHE_DIR = Join-Path $env:TEMP 'cha-cup-uv-cache'
npx.cmd --yes vercel pull --yes --environment production
npx.cmd --yes vercel build --prod
```

6. 构建成功后部署到现有生产项目：

```powershell
npx.cmd --yes vercel deploy --prebuilt --prod --yes
```

7. 确认输出包含：

```text
Aliased https://cha-cup-marketing-demo.vercel.app
```

## 线上只读验收

访问：

```text
https://cha-cup-marketing-demo.vercel.app/api/status
```

预期：

```json
{
  "online": true,
  "mode": "live",
  "capabilities": {
    "feishu_read": true,
    "feishu_write": true,
    "semantic_review": true,
    "image_delivery": true
  }
}
```

访问：

```text
https://cha-cup-marketing-demo.vercel.app/api/tasks
```

验收：

- 返回固定 Base 的真实任务。
- 总数与飞书任务表一致。
- 不返回 Secret、Token 或内部堆栈。
- 前端“同步方式”显示“实时读取”。
- 前端“语义复核”显示服务端返回的实际状态；未配置时不得宣称在线语义服务已启用。
- 前端任务列表来自飞书，而非仓库快照。

## 故障回退

如果飞书实时读取失败：

1. 不要修改前端伪装成实时。
2. 检查 Vercel Function 日志中的错误码。
3. 优先补足最小权限或 Base 文档应用权限。
4. 如果短时间无法恢复，移除或禁用飞书应用变量，使系统回到 `snapshot`，保证简历链接仍可访问。
5. 不得删除原 Vercel 项目或更换域名。

## 验收标准

- 原 Vercel 域名保持不变。
- `/api/status` 为 `online=true`、`mode=live`。
- `/api/tasks` 能读到真实 Base。
- 页面仍可正常选择案例和预览历史图片。
- 没有敏感变量进入 Git。
- 未执行任何写操作。

## 回报格式

```text
模块 02 状态：完成 / 阻塞
Vercel 账号：tian-timm
生产域名：保持不变 / 异常
状态接口：online=，mode=
飞书读取：成功 / 失败
DeepSeek 状态：启用 / 未启用
写能力状态：启用 / 未启用
Git 是否包含密钥：否
部署地址：
原域名验证结果：
阻塞项：
```

