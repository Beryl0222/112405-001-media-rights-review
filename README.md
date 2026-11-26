# 合成影像短片：文本权利与发布复核台

这里保存用于本地流程联调的**合成文本**记录。记录中的人物、机构、邮件、平台、作品和事件均为演练资料，不对应真实人员、机构、地点、权利状态或业务决定。

本项目只做可重复的文本/关系复核，不提供法律结论，也**不会**根据相似度、舆情热度自动认定侵权。

## 1. 它能防止什么误判

- “备注：已获同意”只是一条待核主张，不会自动变成授权。
- 人物（表演者、近亲属、联系人）、角色、作品、权利类型和许可范围分别用稳定 ID 关联；角色许可不能替代演员/肖像许可，人物同意也不能替代作品或角色权利。
- “训练素材”许可不会被解释成公开发布许可；“仅限本校闭门讲座、不含商业短片”会在范围匹配时明确失败。
- 同一根证据被群聊、转发或多次转述时，只算一个证据/授权基础，不算多份独立授权。
- 已故表演者的不同近亲属/联系人分别保留；亲属关系不自动等于代理权，冲突或未核验时进入待确认事项。
- 权利联系人提出异议只会新增异议和待处理事项，不会删除此前已发生的发布记录。
- 角色改名使用同一稳定实体 ID，并保留旧名/版本；撤下作品使用状态与修订事件，不删除实体、关系、待发内容、事项或发布历史。原审核事项的标题和快照不会被静默改写。
- 系统输出 `ready_for_release` / `hold_for_confirmation` / `not_determined` 等复核状态，不输出“侵权成立”的自动结论。

## 2. 运行环境与测试

需要 Python 3.11+，无第三方运行依赖。

```sh
python3 -m unittest discover -s tests -v
```

## 3. 生成复核报告

查看仓库原始三条演练记录：

```sh
python3 -m reviewdesk review data/case.json
# 等价简洁写法
python3 -m reviewdesk data/case.json
python3 -m case_workbench data/case.json
python3 review.py data/case.json
```

查看覆盖待发内容、范围错配、多名近亲属、异议、历史发布、改名和撤下的完整合成演练卷：

```sh
python3 -m reviewdesk review data/review_board.json \
  --json /tmp/review-board.json \
  --markdown /tmp/review-board.md
```

只输出终端 Markdown：

```sh
python3 -m reviewdesk review data/review_board.json
```

也可以使用函数 API：

```python
from reviewdesk import ReviewDesk, review_file

report = review_file("data/review_board.json")
print(report["decision_summary"])

desk = ReviewDesk.from_file("data/case.json")
report = desk.run_review("REVIEW-1")
print(report.to_json())
```

同一输入、同一 review ID 会生成相同的 JSON；报告不读取系统当前时间，也不使用随机数。

## 4. 可办理的审核动作

```python
from reviewdesk import ReviewDesk

desk = ReviewDesk.from_file("data/review_board.json")
desk.run_review("REVIEW-BEFORE")  # 先稳定登记首次发现的事项

# 角色改名：稳定 ID 不变，旧名进入 aliases，并追加修订事件。
desk.rename_entity(
    "C-8",
    "秋衡",
    source_record_id="M-31",
    note="编辑修订角色名，表演者关系不变",
)

# 撤下作品：只改状态并留痕，不删除 W-2。
desk.remove_work("W-2", source_record_id="EDIT-7")

# 待发内容删除被撤下的作品引用；旧版本仍保存在 item.history。
desk.revise_release_item(
    "I-1",
    new_revision="draft-4",
    work_ids=[],
)

# 记录此前已经发生的发布；ID 重复会报错，不能覆盖历史。
desk.record_publication(
    id="PUB-2026-001",
    item_id="I-1",
    published_at="2026-11-01",
    channel="streaming",
)

# 权利联系人异议：新增 open matter，并阻止后续放行判断。
desk.add_objection(
    id="OBJ-2",
    contact_id="CT-2",
    target_item_ids=["I-1"],
    reason="授权渠道超出约定",
)

report = desk.run_review("REVIEW-AFTER-EDIT")
```

审核员在 `report["impacts"]` 中查看每次改名/撤下影响到的待发内容、旧版本、既有事项、发布记录和旧报告；在 `report["matters"]` 中查看仍需处理的事项。事项即使在新规则中不再检出，也只会标记为 `manual_closure_required`，不会被自动关闭或改写。

## 5. JSON 输入结构

所有字段均为可选；`records` 和 `entities` 可直接使用 `data/case.json` 的格式。常用顶层字段：

```jsonc
{
  "case_id": "media-17",
  "records": [
    {"id": "M-1", "kind": "permission-note", "text": "原始文字",
     "refs": {"entity_ids": ["P-1"]}, "root_record_id": "E-1"}
  ],
  "entities": [
    {"id": "P-1", "name": "林岚", "type": "表演者", "labels": ["deceased"]},
    {"id": "C-1", "name": "秋衡", "type": "角色", "aliases": ["秋蘅"]},
    {"id": "W-1", "name": "雾桥", "type": "作品"}
  ],
  "contacts": [
    {"id": "CT-1", "represents_id": "P-1", "relationship": "子女",
     "authority_status": "unverified", "evidence_ids": ["M-2"]}
  ],
  "authorizations": [
    {
      "id": "A-1",
      "person_ids": ["P-1"],
      "character_ids": ["C-1"],
      "work_ids": ["W-1"],
      "right_types": ["likeness", "performance", "character_use", "work_release"],
      "scope": {
        "purposes": ["release"],
        "channels": ["platform_review"],
        "uses": ["short_film"],
        "commercial": true,
        "territories": ["CN"]
      },
      "verification_status": "verified",
      "evidence_ids": ["E-SIGNED-1"],
      "root_authorization_id": "GRANT-1"
    }
  ],
  "release_items": [
    {
      "id": "I-1",
      "title": "待发短片",
      "person_ids": ["P-1"],
      "character_ids": ["C-1"],
      "work_ids": ["W-1"],
      "requested_scope": {"purposes": ["release"], "channels": ["platform_review"]},
      "notes": ["备注：已获同意"]
    }
  ],
  "publications": [
    {"id": "PUB-1", "item_id": "I-1", "published_at": "2026-10-01"}
  ],
  "objections": [
    {"id": "OBJ-1", "contact_id": "CT-1", "target_item_ids": ["I-1"], "status": "open"}
  ],
  "matters": [
    {"id": "MAT-LEGACY-1", "title": "此前已登记事项", "status": "open"}
  ],
  "revisions": []
}
```

输入也接受 `licenses` / `grants`、`items` / `drafts`、`rights_contacts` 等常见别名，以及“记录 / 实体 / 授权 / 待发内容 / 异议 / 事项”等中文顶层键。

## 6. 报告中的关键位置

- `items[].decision`：`ready_for_release` 或 `hold_for_confirmation`。
- `items[].requirement_reviews[]`：每个“权利类型 → 稳定主体 ID”的匹配状态、授权依据和范围缺口。
- `claims[]` / `evidence_groups[]`：原始主张、转述标记和证据去重组。
- `authorization_bases[]`：按根授权/相同内容归并后的独立授权基础。
- `contacts[]`：近亲属/联系人及其核验/冲突状态。
- `relations[]`：饰演、角色属于作品、联系人代表、内容要求权利等有证据的关系。
- `impacts[]`：改名、撤下、修订对待发内容、事项和历史记录的影响。
- `matters[]`：既有事项及当前规则是否仍检出；不会静默关闭。
- `publication_history[]`：只追加的发布记录，异议不会删除其中条目。
- `infringement`：固定为 `not_determined`，并列出被排除的相似度/热度信号。

## 7. 文本解析的保守边界

内置中文规则只处理演练材料中的明确说法：

- 精确名称或已登记别名可以关联稳定实体。
- “林老师”这类姓氏称谓只生成唯一候选或歧义候选，不直接当作本人。
- 代词“她/他/他们”保持未解析，不能替代权利人。
- “转发、听说、据称、群聊”等会标记为传闻；传闻中的授权不会生成已核验授权。
- “已获同意”只生成 `generic_consent_note` 待核主张。
- 范围解析能识别“训练、发布、商业短片、闭门讲座、平台/流媒体渠道、不含/禁止”等演练措辞；复杂文本应优先使用结构化 `scope`。

## 8. 目录

- `reviewdesk/`：领域模型、文本摄入、复核引擎、状态台和 CLI。
- `case_workbench/`：与项目分发包名对应的兼容导入/CLI 别名。
- `tests/test_review_desk.py`：核心规则的可重复单元测试。
- `data/case.json`：最初的三条稳定合成记录。
- `data/review_board.json`：覆盖完整审核流程的合成演练卷。
