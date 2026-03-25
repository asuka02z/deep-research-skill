# Deep Research Python — 运行原理

## 核心设计思路

整个系统的核心矛盾是：**AI 擅长搜索和写作，但不擅长流程控制**。让 AI 自己决定"下一步干啥"很容易跑偏、丢状态、忘记做过什么。

所以这套系统把工作拆成了两半：

- **Python 状态机**：一个完全确定性的程序，掌控所有流程逻辑。它不联网、不写文章，只负责三件事——决定下一步该做什么、校验 AI 交回来的结果、管理引用编号。
- **AI Agent**：听从 Python 的指令，干三类活——联网搜资料、规划报告大纲、写正文。

两者通过一个 CLI（命令行接口）通信。Python 每次输出一个 JSON，告诉 Agent"你现在该做 X"，Agent 做完后调用 `complete` 把结果交回来，Python 校验、更新状态、再吐出下一个 JSON。如此循环直到报告写完。

这种设计的好处是：AI 崩了、断了、被 kill 了都没事，`state.json` 里记着当前进度，随时可以从断点恢复。

## Skill 代码结构

```
.cursor/skills/deep-research-python/
├── SKILL.md.disabled            # Skill 定义文件（Agent 接入协议：初始化/恢复、执行循环、Action JSON 格式）
├── agent_cli.py                 # CLI 入口，Agent 与 Python 引擎的桥梁
│                                  三个子命令：init / next / complete
│                                  所有命令统一输出 JSON action descriptor 到 stdout
├── core/
│   ├── engine.py                # 主控制器 Engine 类
│   │                              - init()        → 创建会话，返回第一个 action
│   │                              - next_action()  → 幂等地返回当前待执行 action
│   │                              - complete()     → 处理 Agent 回传的结果，触发状态转移，返回下一个 action
│   │                              - _build_action() → 根据当前状态分发到 6 个 action builder
│   │                              - _finalize()    → 调 report.finalize() 生成最终报告
│   │
│   ├── states.py                # 声明式状态转移表
│   │                              TRANSITIONS dict: (state, trigger) → lambda → (next_state, updates)
│   │                              transition()    → 执行转移 + 步数计数 + 140 步硬顶
│   │                              InvalidTransition 异常保证不会出现未定义跳转
│   │
│   ├── store.py                 # 持久化层 StateStore 类
│   │                              state.json / citations.json 的原子读写（先写 .tmp 再 rename）
│   │                              initialize() 创建目录结构并清理旧文件
│   │
│   ├── citation.py              # 引用管理 CitationManager 类
│   │                              assign_citations() → 解析 retrieved/*.txt，去重，
│   │                              分配 textidN，回写文件，更新 citations.json
│   │
│   ├── passage.py               # ===PASSAGE=== 格式解析器
│   │                              Passage 数据类（url, title, text, citation_id）
│   │                              parse_passages() / write_passages()
│   │
│   ├── prompts.py               # Prompt 模板引擎
│   │                              render_template() → 加载 prompts/*.txt，用 {variable} 占位符替换
│   │
│   ├── report.py                # 终稿组装（done 阶段的全部逻辑）
│   │                              validate_and_repair() → 5 项校验修复（跨章引用泄露、重复引用、
│   │                                                       Markdown 标题、空章节、元数据修复）
│   │                              assemble_report()     → 扁平章节 → 嵌套结构 → 格式化 Markdown
│   │                              convert_citations()   → [[textidN]] → [1] 顺序编号 + URL 去重合并
│   │                              build_references_section() → 生成参考文献列表
│   │                              finalize()            → 串联以上所有步骤，输出 report.md
│   │
│   └── validation.py            # Agent 输出校验规则
│                                  validate_init_plan()    → 标题非空、3-12 章、无子级位置
│                                  validate_extend_plan()  → 目标存在、深度 ≤3 级、2-5 个子章节
│                                  validate_write_content() → 长度 ≥50、无标题符号、有引用、无重复引用
│
└── prompts/                     # Prompt 模板（纯文本 + {variable} 占位符）
    ├── analyze_query.txt        # 焦点提炼：提取核心问题 + 2-4 个研究维度
    ├── search.txt               # 联网搜索：生成关键词、调 WebSearch、写 ===PASSAGE=== 文件
    ├── init_plan.txt            # 大纲规划：根据背景资料输出 JSON 格式章节列表
    ├── write.txt                # 章节撰写：读素材、写正文、标注 [[textidN]] 引用
    ├── extend_plan.txt          # 迭代深化：覆盖度审计 + 决定是否拆分子章节
    └── done.txt                 # 收尾模板（当前由 Python 直接处理，未经 Agent）
```

### 模块间调用关系

```
agent_cli.py
    └─→ Engine (engine.py)
            ├─→ StateStore (store.py)        # 读写 state.json / citations.json
            ├─→ CitationManager (citation.py) # 搜索完成后分配引用 ID
            │       └─→ passage.py            # 解析/写入 ===PASSAGE=== 文件
            ├─→ render_template (prompts.py)  # 为每个状态生成 Agent prompt
            ├─→ validate_* (validation.py)    # 校验 init_plan / extend_plan 的 JSON 输出
            ├─→ transition (states.py)        # 执行状态转移
            └─→ finalize (report.py)          # done 状态：校验修复 + 拼装报告
```

## 六个状态，一条主线

整个流程是一个有限状态机，一共 6 个状态：

```
┌──────────────┐
│ analyze_query│  ← 起点：分析用户问题，提炼研究焦点
└──────┬───────┘
       │ focus_extracted
       ▼
┌──────────────┐
│    search    │  ← 第一次搜索：搜大纲方向的背景资料
│ (cursor=大纲) │
└──────┬───────┘
       │ outline_searched
       ▼
┌──────────────┐
│  init_plan   │  ← 根据搜到的资料，规划章节大纲（输出 JSON）
└──────┬───────┘
       │ plan_created
       ▼
┌──────────────┐
│    search    │  ← 第二轮搜索：按每个章节分别搜资料（最多 4 个并发）
│ (cursor=章节) │  ← 如果章节多，会分批搜，每批最多 4 个
└──────┬───────┘
       │ sections_searched
       ▼
┌──────────────┐
│    write     │  ← 逐章撰写，每写完一章存一个 content/{position}.md
└──────┬───────┘
       │ all_complete
       ▼
┌──────────────┐
│ extend_plan  │  ← 审视已写内容，决定要不要把某章拆成子章节深入写
└──────┬───────┘
       │ expanded → 回到 search，搜新子章节的资料，再写，再审视
       │ no_expansion → 结束
       ▼
┌──────────────┐
│     done     │  ← 校验 + 修复 + 拼装最终 report.md
└──────────────┘
```

`extend_plan → search → write → extend_plan` 这个循环最多转 5 轮。加上全局步数上限 140 步，保证不会无限循环。

## 每个状态里具体发生了什么

### 1. analyze_query — 提炼焦点

Python 把用户的原始问题塞进一个 Prompt 模板，让 Agent 提炼出一个"焦点陈述"（focus statement），包含核心问题和 2-4 个研究维度。

比如用户问"全球新能源汽车市场分析"，Agent 可能提炼出：

> 全球新能源汽车市场的现状与趋势。聚焦维度：1）市场规模与增速；2）技术路线对比；3）各国政策驱动；4）产业链竞争格局

这个焦点陈述会贯穿后续所有环节，让搜索和写作始终围绕主题。

### 2. search — 联网搜索

搜索分两种场景：

- **搜大纲背景**（cursor="outline"）：第一次搜索，还没有章节，就围绕主题搜 20 条相关信息
- **搜章节素材**（cursor=具体章节编号）：大纲定了之后，按每个章节的标题和计划去搜，每章搜 10 条

搜索由 subagent（子 Agent）执行，可以并行。每个 subagent 拿到 Prompt 后，用 WebSearch 工具搜几组关键词，挑出最相关的段落，按固定格式写到 `retrieved/{position}.txt` 文件里：

```
===PASSAGE===
URL: https://example.com/article
TITLE: 某篇文章的标题
这里是正文段落内容...

===PASSAGE===
URL: https://another.com/report
TITLE: 另一篇报告
另一段内容...
```

subagent 写完文件后，**Python 接管**：逐条解析这些段落，给每条分配一个全局唯一的引用 ID（textid1、textid2...），写回文件，同时更新 `citations.json` 注册表。这保证了引用编号不重复、可追溯。

### 3. init_plan — 制定大纲

Python 把搜到的背景资料 + 焦点陈述喂给 Agent，让它输出一个 JSON 格式的大纲：

```json
{
  "title": "报告标题",
  "sections": [
    {"position": "1", "title": "章节标题", "plan": "写作计划..."},
    {"position": "2", "title": "章节标题", "plan": "写作计划..."}
  ]
}
```

Python 拿到后会做校验（`validation.py`）：章节数量在 3-12 个之间？有没有重复位置？JSON 格式对不对？校验不过就触发 `parse_failed`，让 Agent 重新来。

### 4. write — 逐章撰写

一个 subagent 拿到所有待写章节的列表，按顺序逐章写：

1. 读 `retrieved/{position}.txt` 获取这章的搜索素材和引用 ID
2. 写正文，用 `[[textid5]]` 或 `[[textid3, textid7]]` 格式标注引用
3. 存到 `content/{position}.md`
4. 更新 `state.json` 里这章的 `completed = true`

写完所有章节后，Python 做一次文件系统同步检查——扫描 `content/` 目录，把确实有文件的章节标记为完成（防止 Agent 写了文件但没来得及更新状态就崩了的情况）。

### 5. extend_plan — 迭代深化

这是让报告有深度的关键机制。Agent 审视当前的大纲和已写内容，做一个"覆盖度审计"：

- 对照焦点陈述里的每个维度，逐一检查覆盖程度（deep / shallow / missing）
- 如果有维度覆盖不足，就选一个最相关的已完成章节，拆成 2-5 个子章节

比如原来第 3 章是"技术路线对比"，Agent 觉得写得太粗，就拆成：
- 3.1 纯电技术路线
- 3.2 混动技术路线
- 3.3 氢燃料电池路线

Python 把这些子章节追加到大纲里，然后流程回到 search → write，搜资料、写内容。最多迭代 5 轮。

### 6. done — 收尾组装

所有章节写完后，Python 独立完成最后的收尾工作，不需要 AI 参与：

**校验修复**（`validate_and_repair`）：
- 检查引用 ID 是否跨章节泄露（A 章引用了只在 B 章搜索结果里出现的 ID）
- 去除重复引用（`[[textid3]][[textid3]]` → `[[textid3]]`）
- 清除正文里多余的 Markdown 标题符号
- 修复引用元数据缺失（从原始搜索文件里回填 URL 和标题）
- 删除无法修复的引用

**报告拼装**（`assemble_report`）：
- 把扁平的章节列表转成嵌套结构（支持三级：章 → 节 → 小节）
- 清理标题里的编号前缀
- 把 `[[textid5]]` 转成 `[1]` 这种顺序编号，相同 URL 的引用合并成一个编号
- 在末尾生成参考文献列表
- 输出 `report.md`

## 数据怎么存的

每次调研会话的数据全在一个目录里，没有数据库：

```
.deep-research/<run-id>/
├── state.json         当前状态快照（所有状态信息都在这一个文件里）
├── citations.json     引用注册表（textidN → URL + 标题 的映射）
├── retrieved/
│   ├── outline.txt    大纲阶段搜到的背景资料
│   ├── 1.txt          第 1 章的搜索素材
│   ├── 2.txt          第 2 章的搜索素材
│   ├── 3.1.txt        第 3.1 节的搜索素材（扩展后产生）
│   └── ...
├── content/
│   ├── 1.md           第 1 章的正文
│   ├── 2.md           第 2 章的正文
│   └── ...
└── report.md          最终报告（done 阶段生成）
```

`state.json` 长这样：

```json
{
  "user_query": "用户的原始问题",
  "focus_statement": "提炼后的焦点陈述",
  "state": "write",           // 当前状态
  "cursor": "3",              // 当前处理到哪个章节
  "step": 12,                 // 已执行步数（上限 140）
  "extend_time": 1,           // 已迭代扩展次数（上限 5）
  "citation_counter": 47,     // 引用计数器（保证 ID 全局唯一）
  "survey": {
    "title": "报告标题",
    "sections": [
      {"position": "1", "title": "...", "plan": "...", "completed": true},
      {"position": "2", "title": "...", "plan": "...", "completed": false}
    ]
  }
}
```

## 状态转移表

所有合法的状态跳转都写死在一张表里（`states.py`），以 `(当前状态, 触发器) → 下一状态` 的形式定义：

| 当前状态 | 触发器 | 下一状态 | 说明 |
|---------|--------|---------|------|
| analyze_query | focus_extracted | search | 焦点提炼完毕，去搜背景 |
| search | outline_searched | init_plan | 背景搜完，去规划大纲 |
| search | sections_searched | write | 所有章节素材搜齐，去写 |
| search | batch_remaining | search | 还有章节没搜完，继续搜下一批 |
| init_plan | plan_created | search | 大纲生成成功，去搜各章素材 |
| init_plan | parse_failed | init_plan | 大纲格式不对，重来 |
| write | all_complete | extend_plan | 全写完了，看看要不要扩展 |
| write | partial_complete | write | 还有没写完的章节，接着写 |
| write | missing_retrieved | search | 发现某章缺搜索素材，回去搜 |
| extend_plan | expanded | search | 决定扩展，去搜新子章节的素材 |
| extend_plan | no_expansion | done | 不需要扩展，收工 |
| extend_plan | parse_failed | extend_plan / done | 格式错了重试，超 5 次直接结束 |

不在这张表里的组合会直接报错，不存在"AI 自己决定跳到哪"的情况。

## 容错机制

- **崩溃恢复**：所有进度存在 `state.json` 和文件系统里，随时可以用 `next` 命令从断点继续
- **文件系统同步**：write 阶段结束后，Python 会扫描 `content/` 目录，把写了文件但没更新状态的章节补标为完成
- **原子写入**：`state.json` 和 `citations.json` 先写临时文件再 rename，不会出现写到一半的损坏文件
- **步数上限**：全局 140 步硬顶，无论什么状态都强制进入 done
- **扩展次数上限**：extend_plan 最多 5 轮，防止无限展开
- **校验重试**：init_plan 和 extend_plan 如果 Agent 输出的 JSON 格式不对，会触发 parse_failed 让它重试
