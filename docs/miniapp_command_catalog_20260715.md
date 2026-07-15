# MiniApp 命令迁移目录与验证清单

本目录以 2026-07-15 确认的游戏侧分类为上位口径，代码源为
`model/features/miniapp_command_catalog.py`。它描述命令应该归入哪个交互面，
不直接开启自动化、不改变发送策略，也不代表“已整合”命令已经被本地脚本逐项接管。

## 五类口径

- `integrated`：游戏已整合到统一 MiniApp 信息架构。
- `sect_locked`：宗门专属，统一页面不能绕过宗门资格。
- `external_miniapp`：独立外部 MiniApp 入口，自动化支持度逐项核对。
- `pending_migration`：游戏侧待迁移，当前不应假设已有 HTTP 协议。
- `chat_preserved`：明确保留群内交互，不做普通 MiniApp 批量替代。

`.鬼赌坊` 同时存在外部入口与群内牌局，是当前唯一批准的双入口命令。
现有本地 `.世界boss` MiniApp flow 未出现在本次游戏侧清单中，验证器保持告警，
等待确认后再归类，不能擅自加入任一分类。

## 自动验证

```bash
.venv/bin/python tools/miniapp_command_catalog_report.py
.venv/bin/python tools/miniapp_command_catalog_report.py --json
.venv/bin/python -m pytest -q tests/test_miniapp_command_catalog.py tests/test_miniapp_entry_probe.py
```

检查项：

1. 所有命令以 `.` 开头且空白规范化。
2. 同一命令不得跨分类重复，批准的双入口除外。
3. 已注册 flow plan 的 `replaces_commands` 必须出现在目录中。
4. 外部 MiniApp 命令必须明确区分“已自动化”和“仅登记未自动化”。
5. 群内保留命令不得因为名称出现在 MiniApp 页面而自动改走 HTTP。
6. 宗门专属命令不得绕过本地宗门资格和角色边界。
7. 新增命令时同步更新目录、验证器测试和 `/api/miniapp-status` 快照。

## 维护流程

1. 先更新 `COMMAND_GROUPS`，保留游戏侧原始命令文本。
2. 若命令需要双入口，显式加入 `ALLOWED_MULTI_SURFACE`，禁止靠重复数据隐式放行。
3. 若新增本地 adapter/flow，补 `replaces_commands`、`state_outputs` 和入口支持状态。
4. 运行报告，先处理 `error`，再人工复核 `warn`；`info` 表示已登记但尚未自动化。
5. 协议实现仍遵守 lab-first、脱敏 capture、非幂等 POST 不重试、90 请求/分钟和默认关闭。
6. 生产上线必须另行审计测试；修改目录本身不构成上线授权。
