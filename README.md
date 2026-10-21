# 基于 MongoDB 的问卷系统（大作业一 · 第一阶段）

一个简化版在线问卷系统：注册用户创建问卷、通过链接分发、登录用户填写、按回答跳转、自动统计。
后端 Flask + MongoDB，前端为 Jinja2 服务端渲染 + 少量原生 JavaScript。

## 技术栈

| 层 | 选择 | 说明 |
| --- | --- | --- |
| 数据库 | MongoDB 7.x | 全部业务数据，四个集合，`$jsonSchema` 校验器兜底 |
| 后端 | Python 3.10+ / Flask 3 | 蓝图拆分：认证 / 问卷管理 / 填写 / 统计 |
| 驱动 | pymongo 4.x | 直接使用聚合管道，不引入 ODM |
| 前端 | Jinja2 + 原生 JS | 无构建步骤，填写页单题交互走 JSON 接口 |
| 导出 | openpyxl | 统计结果导出 `.xlsx`，含问卷信息与统计汇总两张表 |
| 测试 | pytest + 真实 MongoDB | 122 个用例，覆盖六类必测项 |

## 快速开始

```bash
# 1. 安装依赖（建议虚拟环境）
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# 2. 确认 MongoDB 已启动（默认 127.0.0.1:27017）
mongosh --eval "db.runCommand({ping:1})"

# 3. 初始化数据库（创建集合、校验器、索引，可重复执行）
python schema/init_db.py

# 4. 启动
python run.py
# 打开 http://127.0.0.1:5000 ，先注册一个账号
```

Windows 用户也可以直接双击 `run.bat`（会自动检查依赖并启动）。

### 配置项（环境变量，均可选）

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://127.0.0.1:27017` | MongoDB 连接串 |
| `MONGO_DB` | `survey_system` | 数据库名 |
| `SECRET_KEY` | 开发用固定值 | Flask 会话签名密钥，部署时务必修改 |
| `MAX_QUESTIONS_PER_SURVEY` | `50` | 单份问卷题目上限 |

测试使用独立的 `survey_system_test` 库，不会动业务数据。

### 让手机访问（同一 Wi-Fi 下演示）

默认只监听 `127.0.0.1`，手机是进不来的。演示时改成监听所有网卡：

```powershell
$env:HOST="0.0.0.0"
python run.py
```

然后查电脑的局域网地址（`ipconfig`，看 WLAN 的 IPv4，通常形如 `192.168.1.23`），
手机浏览器打开 `http://192.168.1.23:5000`。前提是手机与电脑连同一个 Wi-Fi，
且 Windows 防火墙放行 5000 端口（第一次启动时若弹窗选过"取消"，需要手动加规则）：

```powershell
# 管理员 PowerShell
New-NetFirewallRule -DisplayName "Survey System 5000" -Direction Inbound -LocalPort 5000 -Protocol TCP -Action Allow
```

`0.0.0.0` 会把这个开发服务器暴露给局域网，仅用于课堂演示，用完改回默认。


## 功能对照（初始需求）

- 账号：注册、登录、退出；密码用 Werkzeug PBKDF2/Scrypt 加盐哈希存储；登录后只能看到自己的问卷。
- 问卷：标题、说明、是否匿名、是否允许多人多次填写、截止时间、发布、关闭、可分发链接 `/survey/<slug>`。
- 题型：单选、多选（最少 / 最多 / 必须选 N 项）、文本填空（字数上下限）、数字填空（范围、是否整数）。
- 跳转：单选按选项、多选按"包含任意 / 同时包含 / 选中数量"、数字按比较或区间跳转；规则存在数据库里，代码只负责解释。
- 填写：逐题作答、服务端计算下一题、提交时整体重走路径校验；支持并发、匿名、重复提交控制。
- 统计：单/多选计数与百分比、数字平均值/最值/全部取值、文本全部内容；整卷统计与单题统计两个入口。
- 导出：统计页可把整卷或单题导出成 Excel，含「问卷信息」与「统计汇总」两张表，表头冻结、可筛选、横向一页打印。

## 目录结构

```
survey-system/
├─ run.py                     启动入口
├─ run.bat                    Windows 一键启动
├─ config.py                  配置（环境变量覆盖）
├─ requirements.txt
├─ app/
│  ├─ __init__.py             应用工厂、模板过滤器
│  ├─ db.py                   连接、集合校验器、索引
│  ├─ logic.py                校验与跳转引擎（纯函数，可单测）
│  ├─ auth.py                 注册 / 登录 / 退出
│  ├─ surveys.py              问卷与题目管理
│  ├─ fill.py                 填写页面 + 作答/提交接口
│  ├─ stats.py                聚合统计
│  ├─ export.py               统计导出 Excel
│  ├─ utils.py                登录校验、ObjectId、时间解析
│  ├─ templates/              Jinja2 模板
│  └─ static/                 样式与填写页脚本
├─ schema/init_db.py          建库脚本（--drop / --show）
├─ tests/                     pytest 用例（122 个）
└─ docs/                      设计文档、API、测试用例、报告
```

## 运行测试

```bash
pytest -q                                  # 全部用例
pytest -q tests/test_logic.py              # 只跑纯函数单测
pytest -q tests/test_fill_jump.py -v       # 跳转端到端
```

本机实测：`122 passed`，耗时约 30 秒（MongoDB 7.0.14，Windows/Linux 均可）。

## 常见问题

**连不上 MongoDB**：先确认服务在跑（`mongosh` 或 Windows 服务里查看 MongoDB Server），再看端口是否为 27017；如果改过端口，用 `MONGO_URI` 覆盖。

**数据目录带中文**：Windows 上 `mongod` 对含中文的 `--dbpath` 会报 `NonExistentPath`，把数据目录换成纯英文路径即可（这与本项目无关，是 MongoDB 自身限制）。

**端口 5000 被占用**：改 `run.py` 里的 `port`，或设置环境变量后自行调用 `app.run`。

**想清空数据重来**：`python schema/init_db.py --drop`（会删库，测试环境之外慎用）。

## 文档索引

- [系统设计](docs/01-系统设计.md)
- [数据库设计](docs/02-数据库设计.md)
- [API 说明](docs/03-API说明.md)
- [关键逻辑说明](docs/04-关键逻辑说明.md)
- [测试用例](docs/05-测试用例.md)
- [项目完成报告](docs/06-项目完成报告.md)
- [AI 使用日志](docs/07-AI使用日志.md)
