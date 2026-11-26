"""Conservative, deterministic Chinese/English text intake rules.

The parser only creates candidates.  It never turns "听说……同意" into a
verified license.  Exact declared names can be linked; surname/honorific forms
are returned as ambiguous candidates instead of being merged by similarity.
"""

from __future__ import annotations

import re
from typing import Any

from .model import Claim, Entity, RawRecord, Scope

SCOPE_PHRASES: dict[str, tuple[str, ...]] = {
    "training": ("训练素材", "模型训练", "人工智能训练", "ai训练", "训练", "training"),
    "release": ("公开发布", "正式发布", "发布许可", "发行", "公映", "上线", "播出",
                "release", "publish", "distribution"),
    "platform_review": ("平台复核", "平台审核", "审核渠道", "platform review"),
    "streaming": ("流媒体", "视频平台", "网络平台", "streaming"),
    "social_media": ("社交媒体", "社交平台", "微博", "微信视频号", "抖音", "social media"),
    "broadcast": ("电视台", "广播", "broadcast"),
    "festival": ("电影节", "影展", "festival"),
    "school_closed_lecture": ("本校闭门讲座", "闭门讲座", "校内闭门", "closed lecture"),
    "education": ("教育用途", "教学", "课堂", "讲座", "education", "classroom"),
    "internal": ("内部观摩", "内部使用", "internal"),
    "commercial": ("商业短片", "商业用途", "商用", "营利", "广告", "commercial"),
    "short_film": ("短片", "short film"),
    "clip": ("片段", "剪辑片段", "clip"),
    "still": ("剧照", "静帧", "still"),
    "synthetic_media": ("合成影像", "合成视频", "ai生成", "synthetic media"),
}

HEARSAY_MARKERS = (
    "转发", "听说", "据称", "据传", "群聊", "网传", "疑似", "好像", "似乎",
    "forwarded", "reportedly", "heard that", "rumor",
)

GENERIC_CONSENT_PATTERNS = (
    r"已获(?:得)?同意",
    r"已经同意",
    r"已同意",
    r"均已同意",
    r"都同意了?",
    r"(?<!不)(?<!未)同意了?",
    r"口头同意",
    r"consent\s+obtained",
    r"has?\s+consented",
    r"agreed",
)

NEGATION_WORDS = ("不含", "不包括", "不得", "禁止", "不能用于", "不可用于", "而非", "only for")
GRANT_WORDS = ("授权", "许可", "同意用于", "准予", "grants?", "license")

HONORIFIC_RE = re.compile(r"([一-龥A-Za-z])(老师|先生|女士|小姐|家属|老师的|师傅)")
RENAME_VERBS = r"(?:改名为|更名为|改叫|现名为?|新名为?|更名(?:为)?|改名(?:为)?)"
NEW_NAME_RE_TEMPLATE = r"{old}[^。；;，,：:]{{0,24}}?{verb}\s*([一-龥·A-Za-z0-9]{{2,20}})"


def normalize_token(token: str) -> str:
    return token.strip().lower().replace(" ", "_").replace("-", "_")


def canonicalize_scope_value(value: Any) -> str:
    """Map common Chinese/English scope labels to stable canonical tokens."""

    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    direct = normalize_token(text)
    for canonical, phrases in SCOPE_PHRASES.items():
        if direct == canonical:
            return canonical
        for phrase in phrases:
            if lowered == phrase.lower() or direct == normalize_token(phrase):
                return canonical
    # Compound labels such as "禁止商业用途" still expose their key concept.
    for canonical, phrases in SCOPE_PHRASES.items():
        if any(len(phrase) >= 2 and phrase.lower() in lowered for phrase in phrases):
            return canonical
    return direct


def canonicalize_scope_set(values: Any) -> set[str]:
    from .model import _as_set
    return {canonicalize_scope_value(v) for v in _as_set(values) if str(v).strip()}


def parse_scope_text(text: str) -> Scope:
    """Parse explicit scope words and exclusions from synthetic drill text."""

    scope = Scope(raw_text=text or "")
    if not text:
        return scope
    lowered = text.lower()

    # Split Chinese/English clauses so "不含商业短片" marks a denial rather
    # than accidentally granting "短片".
    clauses = re.split(r"[。；;！!？?\n]|，|,", text)
    for clause in clauses:
        clause_low = clause.lower()
        is_denied = any(mark.lower() in clause_low for mark in NEGATION_WORDS)
        is_exclusive = ("仅授权" in clause or "仅限" in clause or "只用于" in clause
                       or "only" in clause_low)
        for canonical, phrases in SCOPE_PHRASES.items():
            hit = any(phrase.lower() in clause_low for phrase in phrases)
            if not hit:
                continue
            if is_denied:
                if canonical in {"commercial", "short_film", "release", "commercial"}:
                    scope.denied_uses.add(canonical)
                    scope.denied_purposes.add(canonical)
                else:
                    scope.denied_uses.add(canonical)
            else:
                if canonical in {"release", "commercial", "education", "internal",
                                 "training"}:
                    scope.purposes.add(canonical)
                elif canonical in {"platform_review", "streaming", "social_media",
                                   "broadcast", "festival", "school_closed_lecture"}:
                    scope.channels.add(canonical)
                else:
                    scope.uses.add(canonical)
                if is_exclusive and canonical not in {"clip", "short_film"}:
                    # "仅授权用于 X" does not imply any unmentioned purpose.
                    pass

    # Common combined phrase "商业短片" should be one explicit excluded use.
    if "商业短片" in text and any(neg in text for neg in ("不含", "不包括", "不得", "禁止")):
        scope.denied_uses.add("commercial_short")
        scope.denied_purposes.add("commercial")
        scope.commercial = False
    elif "商业" in text and ("授权" in text or "可用于" in text):
        if not any(neg in text for neg in NEGATION_WORDS):
            scope.commercial = True

    if "训练" in text and not any(neg in text for neg in NEGATION_WORDS):
        scope.purposes.add("training")
    if any(word in text for word in ("发布", "发行", "公映", "上线")) and not any(
        neg in text for neg in NEGATION_WORDS
    ):
        scope.purposes.add("release")
    return scope


def is_hearsay(text: str, kind: str | None = None) -> bool:
    if kind and "forward" in kind.lower():
        return True
    return any(marker.lower() in (text or "").lower() for marker in HEARSAY_MARKERS)


def has_generic_consent(text: str) -> bool:
    """Return True for an unnegated "consent obtained/agreed" phrase."""

    value = text or ""
    negation = re.compile(r"(并不|并未|并没有|没有|不|未|没|无|非|否认|拒绝)")
    for pattern in GENERIC_CONSENT_PATTERNS:
        for match in re.finditer(pattern, value, flags=re.I):
            prefix = value[max(0, match.start() - 4):match.start()]
            if negation.search(prefix):
                continue
            return True
    return False


def find_exact_mentions(text: str, entities: list[Entity]) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(not (end <= a or start >= b) for a, b in occupied)

    candidates: list[tuple[str, Entity, int, int]] = []
    for entity in entities:
        for name in entity.all_names():
            if not name or len(name) < 2:
                continue
            start = text.find(name)
            while start != -1:
                candidates.append((name, entity, start, start + len(name)))
                start = text.find(name, start + 1)
    # Long names first to reduce nested label overlap; first occurrence retained.
    for name, entity, start, end in sorted(candidates, key=lambda x: (x[2], -(x[3] - x[2]))):
        if overlaps(start, end):
            continue
        occupied.append((start, end))
        mentions.append({
            "text": name,
            "entity_id": entity.id,
            "entity_type": entity.type,
            "kind": "exact_name",
            "status": "linked",
            "start": start,
            "end": end,
            "basis": "declared_name_or_alias",
        })
    mentions.sort(key=lambda m: m["start"])
    return mentions


def find_candidate_mentions(text: str, entities: list[Entity]) -> list[dict[str, Any]]:
    """Find conservative honorific candidates without asserting identity."""

    results: list[dict[str, Any]] = []
    exact_spans = [(m["start"], m["end"]) for m in find_exact_mentions(text, entities)]

    def in_exact(start: int, end: int) -> bool:
        return any(not (end <= a or start >= b) for a, b in exact_spans)

    people = [e for e in entities if e.type == "person"]
    for match in HONORIFIC_RE.finditer(text or ""):
        token = match.group(0)
        if in_exact(match.start(), match.end()):
            continue
        surname = match.group(1)
        candidates = [e for e in people if e.name.startswith(surname)]
        entry: dict[str, Any] = {
            "text": token,
            "kind": "honorific",
            "start": match.start(),
            "end": match.end(),
            "basis": "surname_honorific_rule",
        }
        if len(candidates) == 1:
            entry.update({
                "entity_id": candidates[0].id,
                "entity_type": "person",
                "status": "candidate_pending_confirmation",
                "reason": "姓氏称谓只有一个候选，但称谓不是稳定身份标识",
            })
        elif len(candidates) > 1:
            entry.update({
                "status": "ambiguous",
                "candidate_ids": [e.id for e in candidates],
                "reason": "多个同姓人物，不能自动合并",
            })
        else:
            entry.update({"status": "unresolved", "reason": "找不到已登记人物"})
        results.append(entry)

    for pronoun in ("她们", "他们", "她", "他"):
        start = (text or "").find(pronoun)
        if start != -1 and not in_exact(start, start + len(pronoun)):
            results.append({
                "text": pronoun,
                "kind": "pronoun",
                "status": "unresolved",
                "start": start,
                "end": start + len(pronoun),
                "reason": "代词不能替代人物或权利人标识",
            })
            break
    return sorted(results, key=lambda m: m["start"])


def record_ref_ids(record: RawRecord) -> dict[str, list[str]]:
    refs = record.refs or {}
    output: dict[str, list[str]] = {"entities": [], "items": [], "authorizations": []}
    for key in ("entity_ids", "entities", "subject_ids"):
        value = refs.get(key)
        if isinstance(value, list):
            output["entities"].extend(str(v) for v in value)
        elif value:
            output["entities"].append(str(value))
    for key in ("item_id", "release_item_id", "item_ids"):
        value = refs.get(key)
        if isinstance(value, list):
            output["items"].extend(str(v) for v in value)
        elif value:
            output["items"].append(str(value))
    for key in ("authorization_id", "license_id", "authorization_ids"):
        value = refs.get(key)
        if isinstance(value, list):
            output["authorizations"].extend(str(v) for v in value)
        elif value:
            output["authorizations"].append(str(value))
    return output


def extract_rename_notice(record: RawRecord, entities: list[Entity]) -> dict[str, Any] | None:
    text = record.text or ""
    for entity in entities:
        if entity.type != "character":
            continue
        for old_name in entity.all_names():
            if len(old_name) < 2 or old_name not in text:
                continue
            pattern = RENAME_VERBS
            regex = re.compile(NEW_NAME_RE_TEMPLATE.format(old=re.escape(old_name), verb=pattern))
            match = regex.search(text)
            if match:
                new_name = match.group(1)
                # Strip common trailing words accidentally captured.
                new_name = re.split(r"(?:角色|老师|女士|先生)$", new_name)[0]
                if new_name and new_name != old_name:
                    return {
                        "entity_id": entity.id,
                        "old_name": old_name,
                        "new_name": new_name,
                        "source_record_id": record.id,
                    }
    return None


def extract_claims_from_record(record: RawRecord, entities: list[Entity]) -> list[Claim]:
    """Return deterministic claim candidates from one raw source."""

    text = record.text or ""
    exact = find_exact_mentions(text, entities)
    candidates = find_candidate_mentions(text, entities)
    explicit = record_ref_ids(record)
    entity_ids = [m["entity_id"] for m in exact if m.get("entity_id")]
    # Candidate links are intentionally not placed into confirmed refs.
    refs: dict[str, list[str]] = {
        "mentioned_entity_ids": list(dict.fromkeys(entity_ids + explicit["entities"])),
        "candidate_entity_ids": [
            m.get("entity_id") for m in candidates
            if m.get("entity_id") and m.get("status") == "candidate_pending_confirmation"
        ],
        "ambiguous_mentions": [
            m.get("text") for m in candidates if m.get("status") == "ambiguous"
        ],
        "unresolved_mentions": [
            m.get("text") for m in candidates if m.get("status") == "unresolved"
        ],
        "item_ids": explicit["items"],
    }
    hearsay = is_hearsay(text, record.kind)
    root_evidence = record.root_record_id

    claims: list[Claim] = []
    claim_no = 0

    def add(kind: str, status: str, **kwargs: Any) -> Claim:
        nonlocal claim_no
        claim_no += 1
        claim = Claim(
            id=f"CLAIM-{record.id}-{claim_no}",
            kind=kind,
            text=text,
            status=status,
            refs=refs,
            source_record_ids=[record.id],
            root_evidence_id=root_evidence or record.id,
            hearsay=hearsay,
            **kwargs,
        )
        claims.append(claim)
        return claim

    # Portrayal relation: requires both a person and a character in a sentence
    # with an explicit performance verb.  Exact names only.
    person_mentions = [m for m in exact if m["entity_type"] == "person"]
    character_mentions = [m for m in exact if m["entity_type"] == "character"]
    if person_mentions and character_mentions and re.search(r"饰演|扮演|出演|饰\b|played|portray", text, re.I):
        add(
            "portrayal",
            "grounded",
            details={
                "person_id": person_mentions[0]["entity_id"],
                "character_id": character_mentions[0]["entity_id"],
                "relation": "person_portrays_character",
                "extraction_basis": "exact_names_and_performance_verb",
            },
        )

    rename = extract_rename_notice(record, entities)
    if rename:
        add("rename", "grounded", details=rename)

    generic_consent = has_generic_consent(text)
    has_grant_word = any(word.lower() in text.lower() for word in GRANT_WORDS)
    if generic_consent and not has_grant_word:
        add(
            "generic_consent_note",
            "unverified",
            relay_depth=1 if hearsay else 0,
            details={
                "sufficient_for_release": False,
                "missing": [
                    "identified_rights_holder",
                    "exact_subject",
                    "right_type",
                    "scope_purpose_channel_term",
                    "authenticity_or_authority_confirmation",
                ],
                "rule": "generic_consent_sentence_is_not_an_authorization",
            },
        )

    if has_grant_word or (generic_consent and has_grant_word):
        scope = parse_scope_text(text)
        status = "unverified"
        add(
            "license_text",
            status,
            scope=scope,
            relay_depth=1 if hearsay else 0,
            details={
                "is_hearsay": hearsay,
                "may_create_authorization_basis": not hearsay,
                "rule": "license_text_requires_subject_match_and_verification",
            },
        )
    elif generic_consent:
        # Already represented as generic consent; no extra claim needed.
        pass

    if re.search(r"异议|反对|不同意|暂停使用|不是我(?:本人)?(?:发出|同意|授权)|否认|objection|dispute", text, re.I):
        add(
            "objection_text",
            "unverified",
            details={"must_verify_contact_standing": True},
        )

    # Records with no recognized claim still enter the ledger as raw evidence.
    if not claims:
        add(
            "raw_statement",
            "unverified",
            details={"mentions": exact + candidates},
        )
    else:
        for claim in claims:
            claim.details.setdefault("mentions", exact + candidates)
    return claims
