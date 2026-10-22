# API 说明

系统有两类接口：

- **页面路由**：返回 HTML，用表单提交（`application/x-www-form-urlencoded`）。
- **JSON 接口**：填写页使用，路径统一以 `/api/` 开头，请求/响应均为 `application/json`。

所有需要登录的接口在未登录时：页面路由重定向到 `/login`，JSON 接口返回 `401` 且 body 为 `{"ok": false, "error": "请先登录后再填写问卷"}`。

## 1. 页面路由

| 方法 | 路径 | 登录 | 说明 |
| --- | --- | --- | --- |
| GET | `/` | 否 | 已登录跳 `/dashboard`，否则跳 `/login` |
| GET/POST | `/register` | 否 | 注册；用户名 3~32 字符，密码≥6 位 |
| GET/POST | `/login` | 否 | 登录；支持 `?next=/xxx` 回跳（仅允许站内路径） |
| POST | `/logout` | 是 | 清空会话 |
| GET | `/dashboard` | 是 | 我的问卷列表（含题目数、回答数） |
| GET/POST | `/surveys/new` | 是 | 新建问卷：`title`、`description`、`allow_anonymous`、`allow_multiple`、`deadline` |
| GET | `/surveys/<sid>` | 是 | 编辑页：问卷设置、题目列表、发布/关闭/删除、添加题目 |
| POST | `/surveys/<sid>/settings` | 是 | 保存问卷设置 |
| POST | `/surveys/<sid>/publish` | 是 | 发布（至少一题） |
| POST | `/surveys/<sid>/close` | 是 | 关闭 |
| POST | `/surveys/<sid>/delete` | 是 | 删除问卷 + 题目 + 回答 |
| POST | `/surveys/<sid>/questions` | 是 | 添加题目 |
| GET/POST | `/surveys/<sid>/questions/<qid>` | 是 | 编辑单题（含跳转规则） |
| POST | `/surveys/<sid>/questions/<qid>/delete` | 是 | 删除题目并清理悬空跳转 |
| POST | `/surveys/<sid>/questions/<qid>/move` | 是 | `direction=up|down`，与相邻题交换 `order` |
| GET | `/surveys/<sid>/stats` | 是 | 整卷统计 |
| GET | `/surveys/<sid>/stats/<qid>` | 是 | 单题统计 |
| GET | `/surveys/<sid>/stats/export.xlsx` | 是 | 整卷统计导出 Excel |
| GET | `/surveys/<sid>/stats/<qid>/export.xlsx` | 是 | 单题统计导出 Excel |
| GET | `/survey/<slug>` | 是 | 填写页（未发布/过期/已关闭返回 403） |
| GET | `/survey/<slug>/done` | 是 | 提交成功页 |

### 添加/编辑题目的表单字段

| 字段 | 适用题型 | 说明 |
| --- | --- | --- |
| `type` | 全部 | `single` / `multiple` / `text` / `number` |
| `title` | 全部 | 题干，最多 500 字 |
| `required` | 全部 | 出现即视为必答（`on`） |
| `options` | 单选/多选 | 每行一个选项，按行号自动编号 A/B/C… |
| `min_choices` / `max_choices` / `exact_choices` | 多选 | 留空表示不限制 |
| `min_length` / `max_length` | 文本 | 留空表示不限制 |
| `min_value` / `max_value` / `integer` | 数字 | `integer=on` 表示必须整数 |
| `jump_<key>` | 单选/多选 | 该选项命中时跳往的目标：题目 id 或 `end`；`next` 表示不设置 |
| `number_op` / `number_value` / `number_value2` / `number_target` | 数字 | 比较条件与目标，`between` 时用第二个值 |
| `text_equals` / `text_target` | 文本 | 内容完全相等时跳转 |
| `default_next` | 全部 | 规则都没命中时：`next` 继续 / `end` 结束 |

## 2. JSON 接口

### 2.1 逐步作答

`POST /api/surveys/<slug>/answer`

请求体：

```json
{
  "question_id": "66f0c1a2b5e4d3c2a1f00021",
  "value": ["A", "C"],
  "answers": {"66f0c1a2b5e4d3c2a1f00020": "A"}
}
```

`answers` 是**此前已经答过的题**，不含当前题；服务端会把 `question_id: value` 合并进来再算下一题。

成功响应：

```json
{
  "ok": true,
  "finished": false,
  "next": {
    "id": "66f0c1a2b5e4d3c2a1f00024",
    "index": 3, "total": 4,
    "type": "number",
    "title": "你的年龄",
    "required": true,
    "options": [],
    "rules": {"min_value": 0, "max_value": 120, "integer": true}
  },
  "answered": 2
}
```

答完最后一题时 `finished: true`，`next` 为 `null`。

失败响应（`400`）：

```json
{
  "ok": false,
  "error": "最多选择 2 项，当前选了 3 项",
  "errors": [{"question_id": "...", "message": "最多选择 2 项，当前选了 3 项"}]
}
```

常见错误：`本题为必答题`、`选项 "Z" 不存在`、`必须为整数`、`不能大于 120`、`至少输入 2 个字`。

### 2.2 提交问卷

`POST /api/surveys/<slug>/submit`

```json
{"answers": {"66f0...20": "A", "66f0...24": "25"}}
```

成功（`200`）：`{"ok": true, "response_id": "66f0c1a2b5e4d3c2a1f00031"}`

失败：

| 状态码 | 场景 | 示例 error |
| --- | --- | --- |
| 400 | 校验不通过 | `本题为必答题` / `该题已被跳转规则跳过，不应提交答案` |
| 403 | 未发布、已关闭、已过截止时间 | `该问卷已过截止时间` |
| 409 | 不允许重复提交且已提交过 | `你已经提交过这份问卷` |
| 404 | slug 不存在 | — |

### 2.3 查看题目（调试用）

`GET /api/surveys/<sid>/questions`（需登录且为问卷所有者）

返回题目数组，包含 `options`、`rules`、`logic`，方便用 curl 检查跳转规则是否正确落库。

```bash
curl -b cookie.txt http://127.0.0.1:5000/api/surveys/66f0.../questions
```

## 3. 导出统计表

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/surveys/<sid>/stats/export.xlsx` | 整卷导出 |
| GET | `/surveys/<sid>/stats/<qid>/export.xlsx` | 单题导出 |

响应为附件下载，`Content-Type` 是
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`，
文件名形如 `课程反馈问卷-统计-20261021.xlsx`（中文名走 RFC 5987 的 `filename*`）。

工作簿固定两张表：

**「问卷信息」**：标题、说明、状态、创建/发布/截止时间、题目数、回答数、匿名与重复填写设置、导出范围、导出时间。

**「统计汇总」**：表头为 `题号 | 题型 | 题干 | 选项 / 取值 | 人数 | 占比 | 统计值`。
单选、多选每个选项一行；数字题每项一行写平均、最小、最大、样本数（值在「统计值」列）；
文本题先给填写人数与平均长度，再逐条列出内容。

占比是数值加 `0.0%` 格式，不是字符串，导出后仍可继续计算。表头冻结、开启筛选，
打印时横向缩到一页宽。没有回答时同样能导出，汇总表顶部会写「本问卷暂无回答，以下题目统计均为 0」。

权限与统计页一致：未登录跳登录页，非本人问卷返回 404。

## 4. 调用示例（完整链路）

```bash
BASE=http://127.0.0.1:5000
J=/tmp/cookies.txt

# 注册 + 登录（用 -c 保存会话）
curl -s -c $J -b $J -X POST $BASE/register \
     -d "username=alice&password=secret123&confirm=secret123"
curl -s -c $J -b $J -X POST $BASE/login \
     -d "username=alice&password=secret123"

# 建卷、加题、发布
curl -s -c $J -b $J -X POST $BASE/surveys/new \
     -d "title=课程反馈&allow_multiple=on"
# 假设问卷 id 为 $SID，题目 id 为 $Q1
curl -s -c $J -b $J -X POST $BASE/surveys/$SID/questions \
     -d "type=single&title=你满意吗&options=满意%0A不满意&required=on"
curl -s -c $J -b $J -X POST $BASE/surveys/$SID/publish

# 作答
curl -s -c $J -b $J -X POST $BASE/api/surveys/$SLUG/answer \
     -H "Content-Type: application/json" \
     -d "{\"question_id\":\"$Q1\",\"value\":\"A\",\"answers\":{}}"

# 提交
curl -s -c $J -b $J -X POST $BASE/api/surveys/$SLUG/submit \
     -H "Content-Type: application/json" \
     -d "{\"answers\":{\"$Q1\":\"A\"}}"
```

> Windows PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，上述命令请在 Git Bash / WSL 中运行，
> 或改用 `curl.exe`。

## 5. 错误处理约定

- 页面路由：业务错误用 flash 消息 + 跳回原页，权限问题统一 `404`，状态/时间不允许时 `403`。
- JSON 接口：始终返回 `{"ok": false, "error": "..."}`，校验错误额外带 `errors` 数组（含出错的 `question_id`）。
- 服务端不抛裸异常给用户：所有可预期的输入错误都在 `logic.py` 里转成 `ValidationError`。
