# BGA Ark Nova Arena Dashboard

一个在本机运行的 Board Game Arena（BGA）《方舟动物园》竞技场数据采集与可视化工具。它读取登录后页面使用的历史分页和每桌“统计资料”，将数据保存到本地 SQLite，并通过 Streamlit 提供可筛选的交互面板。

> 本项目不是 BGA 官方产品，与 Board Game Arena 或《方舟动物园》的发行方无关联。请仅读取你有权访问的数据，并遵守 BGA 的服务条款与合理的访问频率。

## 功能

- 按日期、本人等级分和地图筛选对局
- 总胜率与滚动胜率趋势（按时间或对局序号）
- 地图胜率、对手强度与表现分，以及随时间变化的趋势
- 排除未完成或任一方认输后的平均动数、平均每动得分
- 五类行动卡的终局升级率趋势
- 面板内实时刷新，显示历史分页和逐桌统计的读取进度
- 输入任意 BGA 数字玩家 ID；自动识别玩家名并从已保存玩家中快速切换
- 按对手名称/等级分、地图、动数、结果、结束方式和行动卡升级状态检索具体对局，并直接打开 BGA 对局页
- 可选择持久化到本机，或仅在当前 Streamlit 会话中临时读取
- 断点续读：已保存的对局不会重复请求，缺失统计会在后续刷新中继续尝试

## 数据口径

项目明确区分三类数值，避免混用：

- **原始 Elo**：BGA 接口中的内部 Elo 数值。
- **BGA 显示分**：面板中常见的等级分口径，计算为 `原始 Elo - 1300`。
- **竞技场分数**：竞技场赛季积分，单独保存和展示，不参与 Elo 计算。

地图表现分同时考虑胜负与对手强度：

```text
表现分 = 对手平均 BGA 显示分 + 400 × log10((胜场 + 0.5) / (负场 + 0.5))
```

其中 `0.5` 是 Jeffreys 平滑，可避免全胜或全负时得到无穷值。动数、每动得分和行动卡升级率只统计正常结束且双方均未认输的对局。

## 环境要求

- Python 3.11 或更高版本
- Windows PowerShell（使用便捷脚本时）
- 能正常访问 BGA 的网络环境
- 一个由你本人在弹出浏览器中手动登录的 BGA 账号

## 快速开始（Windows）

```powershell
git clone https://github.com/atRNAhzy/bga-arknova-dashboard.git
cd bga-arknova-dashboard
.\setup.ps1
.\dashboard.ps1
```

浏览器打开本地面板后，在侧栏输入个人资料 URL 中的数字玩家 ID，然后点击“刷新并读取最新数据”。首次读取时会打开一个独立 Chromium 窗口；请在该窗口中自行登录 BGA。

也可以直接从 PowerShell 抓取：

```powershell
.\scrape.ps1 -PlayerId <PLAYER_ID>

# 只读取历史列表
.\scrape.ps1 -PlayerId <PLAYER_ID> --history-only

# 调整并发与请求间隔
.\scrape.ps1 -PlayerId <PLAYER_ID> --workers 4 --delay 0.5
```

## 通用安装方式

```bash
python -m venv .venv

# Windows
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m playwright install chromium

# macOS / Linux
.venv/bin/python -m pip install -e .
.venv/bin/python -m playwright install chromium
```

随后运行：

```bash
bga-arknova dashboard
# 或
bga-arknova scrape --player-id <PLAYER_ID>
```

常用抓取参数可通过 `bga-arknova scrape --help` 查看。

## 隐私与安全

项目不会要求你把账号或密码写进配置文件。登录完全在 Playwright 打开的 BGA 页面中进行。

下列内容只应保留在本机，已被 `.gitignore` 排除：

- `data/browser-profile/`：浏览器登录会话和 Cookie
- `data/**/*.sqlite3`：抓取到的玩家与对局数据
- `data/session-temp/`：未保存模式的临时数据
- `.env*`、私钥、缓存和构建产物

发布自己的分支前，仍建议运行：

```powershell
git status --ignored
git diff --cached
```

确认暂存区中没有 `data/`、数据库、浏览器配置、个人导出文件或凭据。若曾误提交凭据，仅删除文件并不够：应立即修改密码/撤销令牌，并清理 Git 历史。

## 数据存储

默认数据位于仓库内的 `data/`，但整个目录不会进入 Git：

```text
data/
├── browser-profile/       # Playwright 持久化登录会话
├── arknova.sqlite3        # 兼容早期版本的默认数据库
├── players/               # 按玩家 ID 分开的数据库
└── session-temp/          # 不保存模式的临时文件
```

如果 BGA 历史分页报告的总数与实际返回的唯一对局数略有差异，工具会保留已取得的数据、给出警告，并在以后刷新时继续补齐，而不会丢弃整次结果。

## 项目结构

```text
src/bga_arknova/
├── cli.py             # 命令行入口
├── scraper.py         # 历史分页与每桌统计采集
├── parser.py          # BGA 字段解析与指标计算
├── db.py              # SQLite schema 与增量写入
└── dashboard.py       # Streamlit 交互面板
tests/                 # 解析与指标测试
```

## 开发与测试

```bash
python -m pip install -e ".[dev]"
pytest -q
```

GitHub Actions 会在 Python 3.11 和 3.13 上运行测试。

## 常见问题

**刷新提示未登录**  
运行一次抓取命令，或从面板刷新，在弹出的 Chromium 中完成登录。登录过期后重新登录即可。

**读取速度慢或出现限流**  
降低 `--workers` 并提高 `--delay`。首次读取需要访问每一桌的统计资料，后续增量刷新会快很多。

**部分地图、动数或行动卡为空**  
这通常表示对应桌的“统计资料”未返回或页面结构发生变化。面板会把它标记为缺失，不会用零值扭曲统计。

**为什么没有完整公开 API 说明？**  
BGA 的相关页面接口没有稳定的公开文档。本项目只使用登录后页面自身请求的数据结构，因此 BGA 更新页面后，解析逻辑可能需要同步调整。

## License

[MIT](LICENSE)
