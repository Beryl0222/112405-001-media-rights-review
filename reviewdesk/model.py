"""Immutable-ish domain records for the review desk.

Dataclasses are intentionally permissive in their ``from_dict`` helpers: drill
JSON may use a few common aliases, while the review engine works with stable
normalized objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Constants / vocabularies
# ---------------------------------------------------------------------------

class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class EntityType(StrEnum):
    PERSON = "person"
    CHARACTER = "character"
    WORK = "work"
    ORGANIZATION = "organization"
    SCOPE = "scope"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    PENDING = "pending"
    VERIFIED = "verified"
    CONFIRMED = "confirmed"
    CONFLICTED = "conflicted"
    REJECTED = "rejected"


class Decision(StrEnum):
    READY = "ready_for_release"
    HOLD = "hold_for_confirmation"
    NOT_EVALUABLE = "not_evaluable"


ENTITY_TYPE_ALIASES = {
    "person": EntityType.PERSON,
    "people": EntityType.PERSON,
    "人物": EntityType.PERSON,
    "个人": EntityType.PERSON,
    "自然人": EntityType.PERSON,
    "表演者": EntityType.PERSON,
    "演员": EntityType.PERSON,
    "逝者": EntityType.PERSON,
    "已故表演者": EntityType.PERSON,
    "权利联系人": EntityType.PERSON,
    "近亲属": EntityType.PERSON,
    "亲属": EntityType.PERSON,
    "character": EntityType.CHARACTER,
    "role": EntityType.CHARACTER,
    "角色": EntityType.CHARACTER,
    "work": EntityType.WORK,
    "title": EntityType.WORK,
    "作品": EntityType.WORK,
    "短片": EntityType.WORK,
    "organization": EntityType.ORGANIZATION,
    "org": EntityType.ORGANIZATION,
    "机构": EntityType.ORGANIZATION,
    "scope": EntityType.SCOPE,
    "许可范围": EntityType.SCOPE,
}

VERIFIED_STATUSES = {
    VerificationStatus.VERIFIED.value,
    VerificationStatus.CONFIRMED.value,
    "clear",
    "cleared",
}

SCOPE_CHANNEL_TOKENS = {
    "platform_review", "streaming", "social_media", "broadcast",
    "festival", "school_closed_lecture",
}
SCOPE_PURPOSE_TOKENS = {
    "training", "release", "education", "internal", "commercial",
    "publication", "research",
}
SCOPE_USE_TOKENS = {
    "short_film", "clip", "still", "synthetic_media", "commercial_short",
    "training_material",
}

BLOCKED_AUTH_STATUSES = {"revoked", "expired", "withdrawn", "rejected"}


def normalize_entity_type(value: Any) -> str:
    if value is None:
        return EntityType.UNKNOWN.value
    text = str(value).strip()
    lowered = text.lower().replace("-", "_").replace(" ", "_")
    if lowered in ENTITY_TYPE_ALIASES:
        return ENTITY_TYPE_ALIASES[lowered].value
    if text in ENTITY_TYPE_ALIASES:
        return ENTITY_TYPE_ALIASES[text].value
    # Chinese labels such as "人物/表演者"
    for alias, entity_type in ENTITY_TYPE_ALIASES.items():
        if len(alias) >= 2 and alias in text:
            return entity_type.value
    return EntityType.UNKNOWN.value


def _as_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {str(v) for v in value if v is not None and str(v).strip()}
    return {str(value)}


def _get(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses/sets/paths to deterministic JSON values."""

    if is_dataclass(value):
        return {
            f.name: to_jsonable(getattr(value, f.name))
            for f in fields(value)
            if f.compare  # ordinary fields; still includes normal data
        }
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(str(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

@dataclass
class ScopeMatch:
    ok: bool
    missing: dict[str, list[str]] = field(default_factory=dict)
    denied_hits: dict[str, list[str]] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Scope:
    """Allowed/denied purpose, channel, use and territory dimensions."""

    purposes: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=set)
    uses: set[str] = field(default_factory=set)
    territories: set[str] = field(default_factory=set)
    allowed_rights: set[str] = field(default_factory=set)
    denied_purposes: set[str] = field(default_factory=set)
    denied_channels: set[str] = field(default_factory=set)
    denied_uses: set[str] = field(default_factory=set)
    commercial: bool | None = None
    raw_text: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, data: Any) -> "Scope":
        if data is None:
            return cls()
        if isinstance(data, Scope):
            return data
        if isinstance(data, str):
            from .ingest import parse_scope_text
            return parse_scope_text(data)
        if isinstance(data, (list, tuple, set, frozenset)):
            return cls.from_dict({"allows": list(data)})
        if not isinstance(data, dict):
            return cls(raw_text=str(data))

        # Common simpler schema: {"allows": [...], "denies": [...]}
        from .ingest import canonicalize_scope_set
        allows = canonicalize_scope_set(_get(data, "allows", "allowed", "allow"))
        denies = canonicalize_scope_set(_get(data, "denies", "denied", "exclude", "exclusions"))
        purposes = canonicalize_scope_set(_get(data, "purposes", "purpose"))
        channels = canonicalize_scope_set(_get(data, "channels", "channel"))
        uses = canonicalize_scope_set(_get(data, "uses", "use", "usage"))
        territories = _as_set(_get(data, "territories", "territory", "regions"))
        rights = _as_set(_get(data, "rights", "right_types", "allowed_rights"))
        for token in allows:
            if token in SCOPE_CHANNEL_TOKENS:
                channels.add(token)
            elif token in SCOPE_PURPOSE_TOKENS:
                purposes.add(token)
            else:
                uses.add(token)
        if denies:
            # A bare denial is retained as both purpose/use denial; dimension
            # specific denial fields are preferable in structured input.
            pass

        commercial = _get(data, "commercial", "is_commercial")
        if commercial is not None:
            commercial = bool(commercial)

        raw = str(_get(data, "raw_text", "text", "source_text", default="") or "")
        scope = cls(
            purposes=purposes,
            channels=channels,
            uses=uses,
            territories=territories,
            allowed_rights=rights,
            denied_purposes=canonicalize_scope_set(_get(data, "denied_purposes")) | denies,
            denied_channels=canonicalize_scope_set(_get(data, "denied_channels")),
            denied_uses=canonicalize_scope_set(_get(data, "denied_uses")) | denies,
            commercial=commercial,
            raw_text=raw,
            note=str(_get(data, "note", default="") or ""),
        )
        # Tolerate callers who put channel/use labels in a generic purposes list.
        for token in list(scope.purposes):
            if token in SCOPE_CHANNEL_TOKENS:
                scope.purposes.discard(token)
                scope.channels.add(token)
            elif token in SCOPE_USE_TOKENS:
                scope.purposes.discard(token)
                scope.uses.add(token)
        if raw and not (scope.purposes or scope.channels or scope.uses):
            from .ingest import parse_scope_text
            parsed = parse_scope_text(raw)
            for name in (
                "purposes", "channels", "uses", "territories",
                "denied_purposes", "denied_channels", "denied_uses",
            ):
                getattr(scope, name).update(getattr(parsed, name))
            if scope.commercial is None:
                scope.commercial = parsed.commercial
        return scope

    def is_empty(self) -> bool:
        return not any([
            self.purposes, self.channels, self.uses, self.territories,
            self.allowed_rights, self.denied_purposes, self.denied_channels,
            self.denied_uses, self.raw_text,
        ]) and self.commercial is None

    def canonical_key(self) -> tuple[Any, ...]:
        return (
            tuple(sorted(self.purposes)),
            tuple(sorted(self.channels)),
            tuple(sorted(self.uses)),
            tuple(sorted(self.territories)),
            tuple(sorted(self.allowed_rights)),
            tuple(sorted(self.denied_purposes)),
            tuple(sorted(self.denied_channels)),
            tuple(sorted(self.denied_uses)),
            self.commercial,
        )

    def allows(self, requested: "Scope") -> ScopeMatch:
        missing: dict[str, list[str]] = {}
        denied_hits: dict[str, list[str]] = {}
        reasons: list[str] = []

        dimensions = ("purposes", "channels", "uses", "territories")
        for dim in dimensions:
            wanted = set(getattr(requested, dim))
            granted = set(getattr(self, dim))
            denied = set(getattr(self, "denied_" + dim, set()))
            if "*" in granted or "all" in granted:
                continue
            hit_denied = sorted(wanted & denied)
            if hit_denied:
                denied_hits[dim] = hit_denied
            absent = sorted(wanted - granted)
            # A denied token is also not granted; report denial separately but
            # avoid noisy duplicate missing entries.
            absent = [x for x in absent if x not in denied]
            if absent:
                missing[dim] = absent

        if requested.commercial is True:
            commercial_allowed = (
                self.commercial is True
                or "*" in self.purposes
                or "commercial" in self.purposes
                or "commercial_short" in self.uses
                or "commercial" in self.uses
            )
            commercial_denied = (
                self.commercial is False
                or "commercial" in self.denied_purposes
                or "commercial" in self.denied_uses
                or "commercial_short" in self.denied_uses
            )
            if commercial_denied:
                denied_hits.setdefault("commercial", []).append("commercial")
            elif not commercial_allowed:
                missing.setdefault("commercial", []).append("commercial_release")

        ok = not missing and not denied_hits
        if denied_hits:
            reasons.append("requested_scope_is_explicitly_excluded")
        if missing:
            reasons.append("requested_scope_is_not_granted")
        if ok and requested.is_empty():
            reasons.append("no_requested_scope_constraints")
        return ScopeMatch(ok=ok, missing=missing, denied_hits=denied_hits, reasons=reasons)

    def to_dict(self) -> dict[str, Any]:
        data = to_jsonable(self)
        data["is_empty"] = self.is_empty()
        return data


# ---------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------

@dataclass
class RawRecord:
    id: str
    text: str
    kind: str | None = None
    source: str | None = None
    refs: dict[str, Any] = field(default_factory=dict)
    root_record_id: str | None = None
    received_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any, index: int = 0) -> "RawRecord":
        if isinstance(data, str):
            return cls(id=f"TEXT-{index + 1}", text=data)
        if not isinstance(data, dict):
            raise TypeError(f"Unsupported record: {data!r}")
        record_id = str(_get(data, "id", "record_id", default=f"R-{index + 1}"))
        text = str(_get(data, "text", "content", "body", default=""))
        refs = _get(data, "refs", "references", "links", default={}) or {}
        if not isinstance(refs, dict):
            refs = {"ids": list(refs) if isinstance(refs, Iterable) else [refs]}
        return cls(
            id=record_id,
            text=text,
            kind=_get(data, "kind", "type", "record_type"),
            source=_get(data, "source", "origin"),
            refs=refs,
            root_record_id=_get(data, "root_record_id", "root_evidence_id", "evidence_id"),
            received_at=_get(data, "received_at", "date", "at"),
            metadata={k: v for k, v in data.items() if k not in {
                "id", "record_id", "text", "content", "body", "kind", "type",
                "record_type", "source", "origin", "refs", "references", "links",
                "root_record_id", "root_evidence_id", "evidence_id",
                "received_at", "date", "at",
            }},
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Entity:
    id: str
    name: str
    type: str = EntityType.UNKNOWN.value
    aliases: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    status: str = "active"  # active | removed | retired | ambiguous
    revised_from: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        entity_id = str(_get(data, "id", "entity_id"))
        name = str(_get(data, "name", "display_name", "label", default=entity_id))
        entity_type = normalize_entity_type(_get(data, "type", "entity_type", "kind"))
        aliases = list(_as_set(_get(data, "aliases", "alias", "former_names", "other_names")))
        labels = [str(x) for x in _as_set(_get(data, "labels", "tags", "roles", "subtypes"))]
        if data.get("deceased") is True or "已故" in str(data.get("type", "")):
            labels.append("deceased")
        return cls(
            id=entity_id,
            name=name,
            type=entity_type,
            aliases=aliases,
            labels=labels,
            status=str(_get(data, "status", default="active")),
            revised_from=_get(data, "revised_from", "supersedes"),
            metadata=dict(_get(data, "metadata", default={}) or {}),
        )

    def all_names(self) -> list[str]:
        names = [self.name, *self.aliases]
        return list(dict.fromkeys(n for n in names if n))

    def has_label(self, *labels: str) -> bool:
        wanted = {x.lower() for x in labels}
        current = {x.lower() for x in self.labels}
        if wanted & current:
            return True
        text = " ".join(self.labels)
        return any(x in text for x in labels if not x.isascii())

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Revision:
    id: str
    kind: str  # rename | remove | restore | revise_item | add_alias
    sequence: int = 0
    entity_id: str | None = None
    item_id: str | None = None
    old_value: Any = None
    new_value: Any = None
    source_record_id: str | None = None
    at: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Claim:
    id: str
    kind: str
    text: str = ""
    status: str = "unverified"  # unverified | grounded | verified | conflicted
    refs: dict[str, list[str]] = field(default_factory=dict)
    source_record_ids: list[str] = field(default_factory=list)
    root_evidence_id: str | None = None
    hearsay: bool = False
    relay_depth: int = 0
    scope: Scope | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Authorization:
    id: str
    right_types: set[str] = field(default_factory=set)
    person_ids: set[str] = field(default_factory=set)
    character_ids: set[str] = field(default_factory=set)
    work_ids: set[str] = field(default_factory=set)
    subject_ids: set[str] = field(default_factory=set)
    grantor_id: str | None = None
    rights_holder_id: str | None = None
    contact_id: str | None = None
    beneficiary_id: str | None = None
    scope: Scope = field(default_factory=Scope)
    evidence_ids: list[str] = field(default_factory=list)
    root_evidence_id: str | None = None
    root_authorization_id: str | None = None
    verification_status: str = VerificationStatus.UNVERIFIED.value
    lifecycle_status: str = "active"
    note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    verified: bool | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "Authorization":
        auth_id = str(_get(data, "id", "authorization_id", "license_id", "grant_id",
                           default=f"AUTH-{index + 1}"))
        scope = Scope.from_dict(_get(data, "scope", "terms", default={}) or {})
        if isinstance(_get(data, "scope_text", "authorization_text"), str):
            parsed = Scope.from_dict(data.get("scope_text") or data.get("authorization_text"))
            scope = Scope.from_dict({**scope.to_dict(), "raw_text": parsed.raw_text})
        verification = _get(data, "verification_status", "verification", "status",
                            "verified_status", default=VerificationStatus.UNVERIFIED.value)
        lifecycle = str(_get(data, "lifecycle_status", "license_status",
                             default="active"))
        # Common input uses status=verified/granted. Treat "granted" as lifecycle,
        # but do not accidentally verify an authorization.
        if verification in {"granted", "active", "valid"} and "verification_status" not in data:
            lifecycle = str(verification)
            verification = _get(data, "verification_status", "verification",
                                default=VerificationStatus.UNVERIFIED.value)
        if data.get("verified") is True:
            verification = VerificationStatus.VERIFIED.value

        person_ids = _id_set(data, "person_ids", "people", "persons", "performers", "person", "performer_id")
        character_ids = _id_set(data, "character_ids", "characters", "roles", "character")
        work_ids = _id_set(data, "work_ids", "works", "titles", "work")
        subject_ids = _id_set(data, "subject_ids", "subjects")
        # Subjects may be dicts with type/id.
        for subject in _as_set(_get(data, "subjects_detail", default=[])):
            if isinstance(subject, dict):
                sid = str(_get(subject, "id", "entity_id"))
                stype = normalize_entity_type(_get(subject, "type", "entity_type"))
                subject_ids.add(sid)
                if stype == EntityType.PERSON.value:
                    person_ids.add(sid)
                elif stype == EntityType.CHARACTER.value:
                    character_ids.add(sid)
                elif stype == EntityType.WORK.value:
                    work_ids.add(sid)
        evidence = list(_as_set(_get(data, "evidence_ids", "evidence", "record_ids",
                                     "source_record_ids")))
        root_evidence = _get(data, "root_evidence_id", "evidence_id", "root_record_id")
        if not root_evidence and evidence:
            root_evidence = evidence[0]
        return cls(
            id=auth_id,
            right_types=_as_set(_get(data, "right_types", "rights", "type", default=[])),
            person_ids=person_ids,
            character_ids=character_ids,
            work_ids=work_ids,
            subject_ids=subject_ids | person_ids | character_ids | work_ids,
            grantor_id=_get(data, "grantor_id", "grantor", "from"),
            rights_holder_id=_get(data, "rights_holder_id", "rights_holder"),
            contact_id=_get(data, "contact_id", "rights_contact_id"),
            beneficiary_id=_get(data, "beneficiary_id", "beneficiary", "to"),
            scope=scope,
            evidence_ids=[str(x) for x in evidence],
            root_evidence_id=str(root_evidence) if root_evidence else None,
            root_authorization_id=_get(data, "root_authorization_id", "root_license_id",
                                       "duplicate_of"),
            verification_status=str(verification),
            lifecycle_status=str(lifecycle),
            note=str(_get(data, "note", "notes", default="") or ""),
            metadata=dict(_get(data, "metadata", default={}) or {}),
            verified=data.get("verified") if isinstance(data, dict) else None,
        )

    def all_subject_ids(self) -> set[str]:
        return set(self.subject_ids) | set(self.person_ids) | set(self.character_ids) | set(self.work_ids)

    def canonical_key(self) -> tuple[Any, ...]:
        root = self.root_authorization_id or self.id
        return (
            root,
            tuple(sorted(self.right_types)),
            tuple(sorted(self.person_ids)),
            tuple(sorted(self.character_ids)),
            tuple(sorted(self.work_ids)),
            self.scope.canonical_key(),
            self.root_evidence_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def _id_set(data: dict[str, Any], *keys: str) -> set[str]:
    raw = _get(data, *keys, default=set())
    result: set[str] = set()
    for value in _as_set(raw):
        if isinstance(value, dict):
            value = _get(value, "id", "entity_id")
        if value is not None:
            result.add(str(value))
    return result


_TOP_LEVEL_ALIASES = {
    "案件编号": "case_id",
    "案件ID": "case_id",
    "原始记录": "records",
    "来源记录": "records",
    "记录": "records",
    "实体": "entities",
    "人物": "people",
    "人员": "people",
    "角色": "characters",
    "作品": "works",
    "授权": "authorizations",
    "授权记录": "authorizations",
    "许可": "authorizations",
    "许可证": "authorizations",
    "权利联系人": "contacts",
    "联系人": "contacts",
    "待发内容": "release_items",
    "待发布内容": "release_items",
    "发布项": "release_items",
    "草稿": "drafts",
    "发布记录": "publications",
    "已发布记录": "publications",
    "异议": "objections",
    "权利异议": "objections",
    "事项": "matters",
    "既有事项": "existing_matters",
    "修订": "revisions",
    "修订事件": "events",
    "关系": "relations",
}


def _normalize_top_level_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for chinese, english in _TOP_LEVEL_ALIASES.items():
        if chinese in normalized and english not in normalized:
            normalized[english] = normalized[chinese]
    return normalized


@dataclass
class Contact:
    id: str
    represents_id: str | None = None
    person_id: str | None = None
    name: str = ""
    relationship: str = ""
    authority_status: str = VerificationStatus.UNVERIFIED.value
    evidence_ids: list[str] = field(default_factory=list)
    contact_info: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    verified: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "Contact":
        cid = str(_get(data, "id", "contact_id", default=f"CONTACT-{index + 1}"))
        evidence = [str(x) for x in _as_set(_get(data, "evidence_ids", "evidence",
                                                "record_ids"))]
        status = str(_get(data, "authority_status", "verification_status", "status",
                          default=VerificationStatus.UNVERIFIED.value))
        if data.get("authority_verified") is True or data.get("verified") is True:
            status = VerificationStatus.VERIFIED.value
        elif data.get("authority_verified") is False and "authority_status" not in data:
            status = VerificationStatus.UNVERIFIED.value
        return cls(
            id=cid,
            represents_id=_get(data, "represents_id", "represents", "deceased_id",
                                "rights_holder_id", "for_person_id"),
            person_id=_get(data, "person_id", "entity_id"),
            name=str(_get(data, "name", "display_name", default="") or ""),
            relationship=str(_get(data, "relationship", "relation", "kinship",
                                  default="") or ""),
            authority_status=status,
            evidence_ids=evidence,
            contact_info=dict(_get(data, "contact_info", "contact", default={}) or {}),
            notes=str(_get(data, "notes", "note", default="") or ""),
            verified=data.get("verified") if isinstance(data, dict) else None,
        )


@dataclass
class RightRequirement:
    id: str
    right_type: str
    subject_type: str
    subject_id: str
    required: bool = True
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class ReleaseItem:
    id: str
    title: str = ""
    revision: str = "draft-1"
    status: str = "pending"
    person_ids: set[str] = field(default_factory=set)
    character_ids: set[str] = field(default_factory=set)
    work_ids: set[str] = field(default_factory=set)
    requirements: list[RightRequirement] = field(default_factory=list)
    scope: Scope = field(default_factory=Scope)
    note_record_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "ReleaseItem":
        item_id = str(_get(data, "id", "item_id", "release_id", "draft_id",
                           default=f"ITEM-{index + 1}"))
        req_data = _get(data, "requirements", "required_rights", default=[]) or []
        requirements: list[RightRequirement] = []
        for j, req in enumerate(req_data):
            if isinstance(req, str):
                requirements.append(RightRequirement(
                    id=f"{item_id}-REQ-{j + 1}", right_type=req,
                    subject_type="any", subject_id="*",
                ))
            elif isinstance(req, dict):
                sid = str(_get(req, "subject_id", "entity_id", "id", default="*"))
                rtype = str(_get(req, "right_type", "right", "type", default="consent"))
                stype = normalize_entity_type(_get(req, "subject_type", "entity_type",
                                                    default="unknown"))
                requirements.append(RightRequirement(
                    id=str(_get(req, "id", default=f"{item_id}-REQ-{j + 1}")),
                    right_type=rtype,
                    subject_type=stype,
                    subject_id=sid,
                    required=bool(_get(req, "required", default=True)),
                    note=str(_get(req, "note", default="") or ""),
                ))
        notes_value = _get(data, "notes", "note", "remarks", "remark", "review_note")
        if isinstance(notes_value, list):
            notes = [str(x) for x in notes_value]
        elif notes_value:
            notes = [str(notes_value)]
        else:
            notes = []
        note_records = [str(x) for x in _as_set(_get(data, "note_record_ids",
                                                      "note_records", "record_ids"))]
        item = cls(
            id=item_id,
            title=str(_get(data, "title", "name", default=item_id)),
            revision=str(_get(data, "revision", "draft_revision", "version",
                              default="draft-1")),
            status=str(_get(data, "status", default="pending")),
            person_ids=_id_set(data, "person_ids", "people", "persons", "person", "performer_ids", "performer_id"),
            character_ids=_id_set(data, "character_ids", "characters", "roles", "character", "role_id"),
            work_ids=_id_set(data, "work_ids", "works", "titles", "work", "work_id"),
            requirements=requirements,
            scope=Scope.from_dict(_get(data, "requested_scope", "scope",
                                       "release_scope", default={}) or {}),
            note_record_ids=note_records,
            notes=notes,
            metadata=dict(_get(data, "metadata", default={}) or {}),
        )
        subjects = _get(data, "subjects", "referenced_entities", "references")
        if subjects:
            for subject in subjects:
                if isinstance(subject, str):
                    sid = subject
                    stype = "unknown"
                    rights: list[str] = []
                elif isinstance(subject, dict):
                    sid = str(_get(subject, "id", "entity_id"))
                    stype = normalize_entity_type(_get(subject, "type", "entity_type"))
                    rights = [str(x) for x in _as_set(_get(subject, "right_types",
                                                           "rights", "required_rights"))]
                else:
                    continue
                if stype == EntityType.PERSON.value:
                    item.person_ids.add(sid)
                elif stype == EntityType.CHARACTER.value:
                    item.character_ids.add(sid)
                elif stype == EntityType.WORK.value:
                    item.work_ids.add(sid)
                for k, rtype in enumerate(rights):
                    item.requirements.append(RightRequirement(
                        id=f"{item_id}-SUBJ-{sid}-{rtype}-{k + 1}",
                        right_type=rtype, subject_type=stype, subject_id=sid,
                    ))
        if not item.requirements:
            item.requirements = _default_requirements(item)
        return item

    def refs(self) -> set[str]:
        refs = set(self.person_ids) | set(self.character_ids) | set(self.work_ids)
        refs.update(r.subject_id for r in self.requirements if r.subject_id != "*")
        return refs

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


def _default_requirements(item: ReleaseItem) -> list[RightRequirement]:
    reqs: list[RightRequirement] = []
    for pid in sorted(item.person_ids):
        reqs.append(RightRequirement(
            id=f"{item.id}-RIGHT-likeness-{pid}",
            right_type="likeness", subject_type=EntityType.PERSON.value, subject_id=pid,
        ))
        reqs.append(RightRequirement(
            id=f"{item.id}-RIGHT-performance-{pid}",
            right_type="performance", subject_type=EntityType.PERSON.value, subject_id=pid,
        ))
    for cid in sorted(item.character_ids):
        reqs.append(RightRequirement(
            id=f"{item.id}-RIGHT-character_use-{cid}",
            right_type="character_use", subject_type=EntityType.CHARACTER.value, subject_id=cid,
        ))
    for wid in sorted(item.work_ids):
        reqs.append(RightRequirement(
            id=f"{item.id}-RIGHT-work_release-{wid}",
            right_type="work_release", subject_type=EntityType.WORK.value, subject_id=wid,
        ))
    return reqs


@dataclass
class PublicationRecord:
    id: str
    item_id: str
    published_at: str | None = None
    revision: str | None = None
    channel: str | None = None
    scope: Scope | None = None
    sequence: int = 0
    note: str = ""
    clearance_decision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "PublicationRecord":
        if isinstance(data, str):
            return cls(id=f"PUB-{index + 1}", item_id=data)
        return cls(
            id=str(_get(data, "id", "publication_id", default=f"PUB-{index + 1}")),
            item_id=str(_get(data, "item_id", "release_item_id", "content_id")),
            published_at=_get(data, "published_at", "at", "date"),
            revision=_get(data, "revision", "draft_revision", "version"),
            channel=_get(data, "channel"),
            scope=Scope.from_dict(_get(data, "scope", default={}) or {}),
            sequence=int(_get(data, "sequence", default=0) or 0),
            note=str(_get(data, "note", default="") or ""),
            clearance_decision=_get(data, "clearance_decision", "decision_snapshot"),
            metadata=dict(_get(data, "metadata", default={}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Objection:
    id: str
    contact_id: str | None = None
    person_id: str | None = None
    target_item_ids: set[str] = field(default_factory=set)
    target_authorization_ids: set[str] = field(default_factory=set)
    target_entity_ids: set[str] = field(default_factory=set)
    reason: str = ""
    record_id: str | None = None
    received_at: str | None = None
    sequence: int = 0
    status: str = "open"  # open | withdrawn | resolved
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.target_item_ids = _as_set(self.target_item_ids)
        self.target_authorization_ids = _as_set(self.target_authorization_ids)
        self.target_entity_ids = _as_set(self.target_entity_ids)

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> "Objection":
        oid = str(_get(data, "id", "objection_id", default=f"OBJ-{index + 1}"))
        return cls(
            id=oid,
            contact_id=_get(data, "contact_id", "rights_contact_id"),
            person_id=_get(data, "person_id", "from_person_id"),
            target_item_ids=_id_set(data, "target_item_ids", "item_ids", "release_item_ids"),
            target_authorization_ids=_id_set(data, "target_authorization_ids",
                                            "authorization_ids", "license_ids"),
            target_entity_ids=_id_set(data, "target_entity_ids", "entity_ids"),
            reason=str(_get(data, "reason", "text", default="") or ""),
            record_id=_get(data, "record_id", "source_record_id"),
            received_at=_get(data, "received_at", "at", "date"),
            sequence=int(_get(data, "sequence", default=0) or 0),
            status=str(_get(data, "status", default="open")),
            events=list(_get(data, "events", default=[]) or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass
class Matter:
    id: str
    title: str
    kind: str = "rights_confirmation"
    status: str = "open"  # open | in_review | resolved
    severity: str = "blocker"
    codes: list[str] = field(default_factory=list)
    refs: dict[str, list[str]] = field(default_factory=dict)
    opened_source: str | None = None
    created_review: str | None = None
    subject_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    def add_event(self, kind: str, note: str = "", source: str | None = None) -> None:
        self.history.append({"kind": kind, "note": note, "source": source})

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


# ---------------------------------------------------------------------------
# Case aggregate
# ---------------------------------------------------------------------------

@dataclass
class Case:
    case_id: str = "case"
    records: list[RawRecord] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    authorizations: list[Authorization] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    release_items: list[ReleaseItem] = field(default_factory=list)
    publications: list[PublicationRecord] = field(default_factory=list)
    objections: list[Objection] = field(default_factory=list)
    matters: list[Matter] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    review_history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "Case":
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Case":
        if not isinstance(payload, dict):
            raise TypeError("Case JSON must be an object")
        payload = _normalize_top_level_aliases(payload)
        case = cls(
            case_id=str(_get(payload, "case_id", "id", default="case")),
            metadata=dict(_get(payload, "metadata", default={}) or {}),
        )

        scope_library: dict[str, Scope] = {}
        for scope_data in _get(payload, "scopes", "license_scopes", "scope_library", default=[]) or []:
            if isinstance(scope_data, Scope):
                scope_obj = scope_data
                scope_id = getattr(scope_obj, "scope_id", None)
            elif isinstance(scope_data, dict):
                scope_obj = Scope.from_dict(scope_data)
                scope_id = _get(scope_data, "id", "scope_id")
            else:
                continue
            if scope_id:
                scope_library[str(scope_id)] = scope_obj
        if scope_library:
            case.metadata["scope_library"] = {
                sid: scope.to_dict() for sid, scope in scope_library.items()
            }

        raw_entities = list(_get(payload, "entities", default=[]) or [])
        for key, forced_type in (
            ("people", EntityType.PERSON.value),
            ("persons", EntityType.PERSON.value),
            ("characters", EntityType.CHARACTER.value),
            ("works", EntityType.WORK.value),
        ):
            for item in payload.get(key, []) or []:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("type", forced_type)
                raw_entities.append(item)
        for i, ent in enumerate(raw_entities):
            if isinstance(ent, Entity):
                case.entities.append(ent)
            elif isinstance(ent, str):
                case.entities.append(Entity(id=ent, name=ent))
            else:
                case.entities.append(Entity.from_dict(ent))

        for i, rec in enumerate(_get(payload, "records", "source_records", "sources",
                                    default=[]) or []):
            case.records.append(RawRecord.from_dict(rec, i))

        for i, auth in enumerate(_get(payload, "authorizations", "licenses", "grants",
                                      "consents", default=[]) or []):
            if isinstance(auth, Authorization):
                case.authorizations.append(auth)
            elif isinstance(auth, str):
                case.authorizations.append(Authorization(id=auth))
            else:
                auth_obj = Authorization.from_dict(auth, i)
                scope_ref = _get(auth, "scope_id", "scope_ref", "scope_ref_id")
                if scope_ref and str(scope_ref) in scope_library and auth_obj.scope.is_empty():
                    auth_obj.scope = scope_library[str(scope_ref)]
                elif isinstance(auth.get("scope"), str) and auth["scope"] in scope_library:
                    auth_obj.scope = scope_library[auth["scope"]]
                case.authorizations.append(auth_obj)

        for i, contact in enumerate(_get(payload, "contacts", "rights_contacts",
                                         default=[]) or []):
            if isinstance(contact, Contact):
                case.contacts.append(contact)
            elif isinstance(contact, str):
                case.contacts.append(Contact(id=contact, name=contact))
            else:
                case.contacts.append(Contact.from_dict(contact, i))

        for i, item in enumerate(_get(payload, "release_items", "items", "drafts",
                                      "pending_content", "submissions", default=[]) or []):
            if isinstance(item, ReleaseItem):
                case.release_items.append(item)
            elif isinstance(item, str):
                case.release_items.append(ReleaseItem.from_dict({"id": item, "title": item}, i))
            else:
                item_obj = ReleaseItem.from_dict(item, i)
                scope_ref = _get(item, "requested_scope_id", "scope_id", "scope_ref")
                if scope_ref and str(scope_ref) in scope_library and item_obj.scope.is_empty():
                    item_obj.scope = scope_library[str(scope_ref)]
                elif isinstance(item.get("scope"), str) and item["scope"] in scope_library:
                    item_obj.scope = scope_library[item["scope"]]
                case.release_items.append(item_obj)

        for i, pub in enumerate(_get(payload, "publications", "published_records",
                                     "release_history", default=[]) or []):
            if isinstance(pub, PublicationRecord):
                case.publications.append(pub)
            else:
                case.publications.append(PublicationRecord.from_dict(pub, i))

        for i, obj in enumerate(_get(payload, "objections", default=[]) or []):
            if isinstance(obj, Objection):
                case.objections.append(obj)
            else:
                case.objections.append(Objection.from_dict(obj, i))

        for matter in _get(payload, "matters", "existing_matters", default=[]) or []:
            if isinstance(matter, Matter):
                case.matters.append(matter)
            else:
                case.matters.append(Matter(
                    id=str(matter["id"]),
                    title=str(matter.get("title", matter["id"])),
                    kind=str(matter.get("kind", "rights_confirmation")),
                    status=str(matter.get("status", "open")),
                    severity=str(matter.get("severity", "blocker")),
                    codes=list(matter.get("codes", [])),
                    refs=dict(matter.get("refs", {})),
                    opened_source=matter.get("opened_source"),
                    created_review=matter.get("created_review"),
                    subject_snapshot=dict(matter.get("subject_snapshot", {})),
                    notes=list(matter.get("notes", [])),
                    history=list(matter.get("history", [])),
                ))

        for rev in _get(payload, "revisions", "events", default=[]) or []:
            if isinstance(rev, Revision):
                case.revisions.append(rev)
            else:
                case.revisions.append(Revision(
                    id=str(rev.get("id", f"REV-{len(case.revisions) + 1}")),
                    kind=str(rev.get("kind", "revision")),
                    sequence=int(rev.get("sequence", len(case.revisions) + 1)),
                    entity_id=rev.get("entity_id"),
                    item_id=rev.get("item_id"),
                    old_value=rev.get("old_value"),
                    new_value=rev.get("new_value"),
                    source_record_id=rev.get("source_record_id"),
                    at=rev.get("at"),
                    note=str(rev.get("note", "") or ""),
                ))

        case.relations = list(_get(payload, "relations", default=[]) or [])

        # Signals that must never be used as rights evidence are retained only
        # as metadata so a caller can see they were not silently dropped.
        for key in ("similarity", "similarity_score", "similarity_scores",
                    "public_sentiment", "heat", "heat_score", "trending",
                    "infringement_score"):
            if key in payload:
                case.metadata.setdefault("excluded_signals", {})[key] = payload[key]
        return case

    def entity(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)

    def record(self, record_id: str) -> RawRecord | None:
        return next((r for r in self.records if r.id == record_id), None)

    def to_dict(self) -> dict[str, Any]:
        return to_jsonable(self)

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
