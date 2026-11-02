# Agent使用手册

用户直接使用图形面板：双击桌面Codex Memory或D:\codex-memory\打开记忆面板.lnk。搜索/筛选、依据、更正记录、增改、暂不提起/恢复、删除都可在窗口内操作；README优先介绍人类用法。本手册保留给Agent和维护排查使用。

设置中可调整主题色、动效开关及75%—150%界面缩放，保存在data/ui-settings.json，不属于正式记忆。Claude Code和DeepSeek Harness的具体连接步骤见README。没有后台语义整理器；跨条整理由当前Agent按用户要求执行，不为本服务另配API Key。

图形面板需要Windows和新版Microsoft Edge，使用完整Edge的PWA窗口，不使用WebView2或其他GUI外框；CLI/MCP不依赖Edge。专用配置在data/ui-edge，不使用日常浏览器配置或恢复其标签页。data/gui-port.json保存首选端口，使本地应用地址稳定；端口被占用时会自动改用空闲端口。窗口右上角使用原生最小化、最大化和关闭按钮，顶部空白处可拖动；重复启动会聚焦已有面板。更新后关闭旧版窗口，再从原快捷方式打开。

数据根目录为`D:\codex-memory`。memory MCP只有一个工具，参数是`action`、`project`和`data`；project传当前项目根的绝对路径，空值只允许召回全局。路径按本机规范化，不同盘/项目不会混合返回。

## 召回

```json
{"action":"search","project":"F:\\code\\codex-memory","data":{"query":"记忆路径"}}
```

search最多5条、返回摘录合计≤1500字符；read/related/sources仅在需要细节、关系或原依据时调用，data为`{"id":"m-..."}`。context默认只取已确认的preference/workflow，最多3条/500字符；不每轮执行。list可浏览当前范围的前5条，status查看数量与损坏提示。

记忆是有出处的参考：当前用户纠正、较新的实证与实际代码优先。provisional是推断，不能装成用户已确认；过期记忆默认不自动召回。命中不等于一定相关，必要时核对sources的时间和原文。

## 保存

只保存对未来工作有用的稳定偏好、决定、工作流程、已验证项目事实/阶段状态；不做聊天沉淀、逐轮摘要或聊天归档。普通问候、测试日志、大段代码、模型建议和未作出的决定不直接成为正式事实。

```json
{
  "action":"save",
  "project":"F:\\code\\codex-memory",
  "data":{
    "key":"memory-location",
    "title":"记忆运行目录",
    "body":"开发源码在F:/code/codex-memory，安装代码和后续记忆在D:/codex-memory。",
    "kind":"workflow",
    "scope":"project",
    "aliases":["记忆位置","安装路径"],
    "importance":3,
    "evidence":{
      "kind":"user",
      "origin":"本轮对话：用户指定源码和运行目录",
      "text":"先在F:/code/codex-memory写代码，写完后放D:/codex-memory，后面记忆也放D:/codex-memory里"
    }
  }
}
```

scope=global用于跨项目适用的用户偏好；scope=project用于当前项目事实。kind为fact/preference/decision/workflow/state/reference之一；来源kind为user（真实用户表述）、verified（现场验证结果）、inference（明确标记的推断）。origin给对话/消息位置、用户提供的文档位置或验证命令与日期，text只保留支持该条的必要短片段，最多1000字，不粘整段聊天记录、不虚构来源。

同一事实用稳定语义key，不用每轮日期或随机编号造重复记忆。related可给已有同范围记忆的ID，aliases便于中文/英文检索。expires_at可指定有时区的ISO8601截止时间；仅在确知时填写。

## 更正和管理

先read获得revision，再更正，避免另一会话的更新被覆盖：

```json
{"action":"revise","project":"F:\\code\\codex-memory","data":{"id":"m-...","expected_revision":1,"body":"更正后的当前事实","evidence":{"kind":"user","origin":"本轮明确更正","text":"用户实际说出的更正原话"}}}
```

revise可更新title/body/kind/aliases/related/importance/expires_at，保留身份、原有证据和历史。版本冲突先重新读取，不盲目改revision重试。save同key而内容不同会拒绝，要求走revise。

mute/unmute/forget的data只含id和expected_revision。只有用户确实要求删除/遗忘时执行forget；它清正式内容、历史和孤立来源，遗忘ID仍保留以抑制复活。恢复遗忘必须有用户的新明确依据，在save里传restore_forgotten=true。旧的自动摘要不能当作恢复授权。外部备份未被自动清理。

## 本轮工具暂未加载时的CLI

```powershell
python -X utf8 D:\codex-memory\memory.py status
python -X utf8 D:\codex-memory\memory.py search '具体主题' --project F:\code\codex-memory
python -X utf8 D:\codex-memory\memory.py read m-填实际ID --project F:\code\codex-memory
python -X utf8 D:\codex-memory\memory.py reindex
# 把上面的JSON请求写入UTF-8文件再执行，避免shell转义破坏原文
python -X utf8 D:\codex-memory\memory.py request C:\完整路径\请求.json
# 也可由stdin传JSON：memory.py request -
```

CLI和MCP使用同一存储/校验/范围规则。更新源码后从`F:\code\codex-memory`运行`python -X utf8 install.py`，data继续保留。

## 验收与边界

本系统没有后台模型和付费调用；主动记忆依赖当前Agent遵循短规则调用save/revise。MCP配置及全局AGENTS在新会话/重新连接时加载，不改动现有对话历史。不会自动从future-bot、其他项目或旧聊天批量导入资料。

本机管理员可直接读写Markdown；正常修改优先用工具以保留revision与证据。手动编辑正文后索引会更新；损坏笔记保留并报告，缺有效来源不召回。损坏派生索引丢弃后重建，原笔记保持。不要将data或install-backups提交Git。
