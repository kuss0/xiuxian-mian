# 2026-10-02 网页后台收敛

## 范围与收益

基线 `c759f193`，独立 worktree `/root/xiuxian-web-consolidation-20261002`。只处理实际被 `index.html` 加载的原生 JS，不接入 `/root/xiuxian-frontend` 的预生产交付，不改 CSS、HTML 布局、API 契约、Python 调度、账号配置和 `ui_write_guard.js`。

- 从 `app.js` 删除后续脚本已完整替代的 `renderModules`、`renderIdentityList`、`renderIdentitySelect`、`renderPassiveInboxPanel`，及仅供旧消息盒子使用的两个格式化函数。现用实现分别归属 `module_cards_ui.js`、`identity_api_ui.js`、`passive_inbox_ui.js`。
- 五个模块通过 `registerRenderHook` 注册刷新后的渲染，取代层层替换 `renderAll`；Map 保持注册顺序，同名注册替换而非叠加。渲染异常仍向调用方抛出，不新增吞错。
- 删除消息盒子的额外包装：核心渲染已有一次调用，原每轮两次实测降为一次。
- `startApp` 在 DOMContentLoaded 后统一启动，移除旧首屏、身份增强和模块 setTimeout 引起的重复全量渲染；启动幂等，轮询仍只有一个、保持原间隔及 silent/keepFlash 参数。加载未完成时不执行不完整渲染。

九个在用 JS 文件从 243,490 字节降至 226,702 字节，净减 **16,788 字节（16.4 KiB）**。没有新增生产依赖。app.js 原来是压缩长行，不用行数虚报收益；新增测试和离线浏览器工具单独计，不算业务减量。没有测量或承诺整体 CPU/内存百分比改善。

## 验证

- 定向 UI 回归：`43 passed`。阴罗旧测试此前检查被覆盖的 app.js 版本，现改为检查现用 module_cards；浏览器另验真实阴罗主开关和动作区。
- Node 实际执行测试：初始化前不渲染、启动幂等、单一轮询、hook 顺序/同名去重、消息盒子仅渲染一次。
- 全量隔离测试：`15650 passed, 1408 subtests passed in 449.88s`。
- HTTP 冒烟：18 项通过，其中 76 条受保护 API 全部拒绝匿名请求。临时状态库、随机回环端口，无游戏请求。
- 全部前端脚本 Acorn 解析通过；Ruff、compileall、diff 检查通过。AST 复核 app.js 保留的原函数只有 `renderAll` 增加启动门禁和渲染 hooks，其余函数体未改。
- 浏览器：1280x900 和 390x844，首页、基础配置、队列、副本、健康、MiniApp 状态、兼容模块共 14 对截图；布局尺寸一致，无横向溢出增加，无页面错误。最终每图最多 40 个边缘像素存在 1/255 色阶差，属于 Chromium 抗锯齿；工具门槛为最多 64 像素且最大差 2/255，不把图片字节不同直接判成布局变化。
- 两尺寸均通过身份切换、一次点击仅一次保存、晚到 silent 刷新不覆盖编辑中表单。基线消息盒子每轮 2 次，新版 1 次。网络全部拦截到内存 fixture，外部请求为 0。
- 完整截图及结果：`/root/xiuxian-web-evidence-20261002/results.json`。可复用工具：`tools/ui_console_browser_smoke.py --baseline-root <旧 worktree> --output-dir <目录>`，依赖已有 Playwright/Chromium，不需要额外图像库。

## 发布与剩余边界

只发布静态文件，Python 服务每次请求从磁盘读取资源，模板原有 asset_version 会随资源修改更新；无需重启游戏进程。旧页面刷新后加载新版。发布前后核对文件 HTTP 内容、服务 PID、watchdog/observer 与账号配置。

CSS 层叠、renderSummary/宗门卡片的语义增强、UI 写保护并发状态和未迁移接口仍有后续整理空间，但不是完全重复实现，本批不硬删、不扩大重构。现有 R2 备份改动保留，不进入本提交。

## 发布验收

- `ba8e80cd` 于 23:50 快进合入生产，已推送 `xiuxian-mian/main`。仅静态文件和测试/说明有改动，没有部署预生产新前端。
- 23:53 对九个已改 JS 分别执行真实 HTTP GET，全部 200，响应与提交后的文件逐字节相等。
- 主服务 supervisor/worker 保持 `405325/405326`，启动时间仍为 22:01:15，`NRestarts=0`；本轮没有停机或重启。23:53 health observer 和 watchdog 均正常，日志无新异常。
- 24 身份模块开关/参数、身份策略/执行窗和全局/MiniApp 配置的规范化摘要发布前后一致，只排除原有群 BOT 活动观测时间字段；没有游戏主动请求或配置写入。
- 三个原有 R2 文件的哈希发布前后一致，仍单独留在工作区。回退只需反向提交本次 JS 改动，不还原状态库，不重启游戏服务；浏览器刷新后生效。
