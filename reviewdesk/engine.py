"""Deterministic release-review engine and report renderer."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .infringement import assess_infringement
from .ingest import extract_claims_from_record, has_generic_consent
from .model import (
    Authorization,
    Case,
    Matter,
    Scope,
    VerificationStatus,
    to_jsonable,
)


RIGHT_ALIASES: dict[str, set[str]] = {
    "likeness": {"likeness", "portrait", "image", "face", "肖像", "肖像权", "形象", "形象权", "脸"},
    "performance": {"performance", "performer", "performer_rights", "表演", "表演权", "表演者", "表演者权", "演出"},
    "character_use": {"character", "character_use", "role", "角色", "角色权", "人物形象", "角色使用"},
    "work_release": {"work", "work_release", "title", "distribution", "copyright", "著作权", "作品", "发行", "信息网络传播权", "片段发布"},
    "synthetic_asset": {"synthetic_asset", "synthetic_media", "training_output", "合成影像", "合成素材", "合成视频"},
    "consent": {"consent", "同意", "授权同意"},
}

STATE_LABELS = {
    "grounded": "已有匹配依据",
    "missing_authorization": "缺少授权依据",
    "pending_confirmation": "有材料但待权利人/代理权确认",
    "scope_not_covered": "许可范围不覆盖本次发布请求",
    "objection_open": "存在未处理权利异议",
    "subject_removed": "对象已被撤下或停用",
    "blocked": "授权状态受阻",
}

DECISION_LABELS = {
    "ready_for_release": "可进入发布流程",
    "hold_for_confirmation": "暂缓，待权利确认或处理事项",
    "not_evaluable": "资料不足，无法复核",
}


def _norm_right(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def canonical_right_type(value: str) -> str:
    norm = _norm_right(value)
    if norm in RIGHT_ALIASES:
        return norm
    raw = str(value or "").strip().lower()
    for canonical, aliases in RIGHT_ALIASES.items():
        normalized = {_norm_right(a) for a in aliases}
        if norm in normalized or raw in {a.lower() for a in aliases}:
            return canonical
        if not norm.isascii() and any(not a.isascii() and a in str(value) for a in aliases):
            return canonical
    return norm


def right_covers(granted_rights: set[str], requested: str) -> bool:
    granted = {_norm_right(r) for r in granted_rights}
    if "*" in granted or "all" in granted or "all_rights" in granted or "all_rights_reserved" in granted:
        return True
    req_key = canonical_right_type(requested)
    if req_key in granted or _norm_right(requested) in granted:
        return True
    aliases = RIGHT_ALIASES.get(req_key, {req_key})
    normalized_aliases = {_norm_right(a) for a in aliases}
    if granted & normalized_aliases:
        return True
    # Chinese labels may be embedded in a longer right description, but only
    # for multi-character explicit terms; generic "consent" never covers all.
    for g in granted:
        for alias in aliases:
            if not alias.isascii() and len(alias) >= 2 and alias in g:
                return True
    return False


def review_case(source: str | Path | dict[str, Any] | Case, *, archive: bool = False) -> "ReviewReport":
    """Convenience entry point used by scripts and callers.

    Plain JSON/path inputs go through :class:`~reviewdesk.desk.ReviewDesk`, so
    explicit edit notices and structured revisions are intake-processed.  An
    existing :class:`Case` is reviewed directly without replacing its entity
    state.
    """

    if isinstance(source, Case):
        return ReviewEngine(source).review(archive=archive)
    if isinstance(source, dict):
        from .desk import ReviewDesk
        desk = ReviewDesk.from_dict(source)
    else:
        from .desk import ReviewDesk
        desk = ReviewDesk.from_file(source)
    return desk.review(archive=archive)


def review_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return review_case(payload).to_dict()


@dataclass
class ReviewReport:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.payload

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.payload, ensure_ascii=False, indent=indent, sort_keys=True)

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json() + "\n", encoding="utf-8")

    def markdown(self) -> str:
        return render_markdown(self.payload)

    def write_markdown(self, path: str | Path) -> None:
        Path(path).write_text(self.markdown() + "\n", encoding="utf-8")


class ReviewEngine:
    """Compute a review without using similarity or popularity signals."""

    def __init__(self, case: Case | dict[str, Any] | str | Path):
        if isinstance(case, Case):
            self.case = case
        elif isinstance(case, dict):
            self.case = Case.from_dict(case)
        else:
            self.case = Case.from_file(case)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewEngine":
        return cls(Case.from_dict(payload))

    @classmethod
    def from_file(cls, path: str | Path) -> "ReviewEngine":
        return cls(Case.from_file(path))

    # ------------------------------------------------------------------
    # Claims / evidence
    # ------------------------------------------------------------------
    def all_claims(self) -> list[Any]:
        claims = list(self.case.claims)
        existing_record_sources = {rid for c in claims for rid in c.source_record_ids}
        for record in self.case.records:
            if record.id in existing_record_sources:
                continue
            claims.extend(extract_claims_from_record(record, self.case.entities))
        return sorted(claims, key=lambda c: c.id)

    def evidence_groups(self, claims: list[Any], auths: list[Authorization] | None = None) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for record in sorted(self.case.records, key=lambda r: r.id):
            key = record.root_record_id or record.id
            group = groups.setdefault(key, {
                "root_evidence_id": key,
                "record_ids": [],
                "relay_record_ids": [],
                "direct_source_record_ids": [],
                "authorization_ids": [],
                "independent_authorization_count": 0,
            })
            group["record_ids"].append(record.id)
            if record.root_record_id and record.root_record_id != record.id:
                group["relay_record_ids"].append(record.id)
            kind = (record.kind or "").lower()
            text = record.text or ""
            is_relay = any(marker in text for marker in ("转发", "听说", "据称", "群聊"))
            if not is_relay and ("permission" in kind or "license" in kind or "授权" in text):
                group["direct_source_record_ids"].append(record.id)

        # Authorization objects tied to an evidence root are the authoritative
        # count; multiple relays/duplicate rows still resolve to one basis.
        for auth in auths or []:
            key = auth.root_evidence_id or (auth.evidence_ids[0] if auth.evidence_ids else None)
            if not key:
                continue
            group = groups.setdefault(key, {
                "root_evidence_id": key,
                "record_ids": [],
                "relay_record_ids": [],
                "direct_source_record_ids": [],
                "authorization_ids": [],
                "independent_authorization_count": 0,
            })
            group["authorization_ids"].append(auth.id)

        for claim in claims:
            key = claim.root_evidence_id or claim.id
            groups.setdefault(key, {
                "root_evidence_id": key,
                "record_ids": [],
                "relay_record_ids": [],
                "direct_source_record_ids": [],
                "authorization_ids": [],
                "independent_authorization_count": 0,
            })
        result = sorted(groups.values(), key=lambda g: g["root_evidence_id"])
        for group in result:
            group["record_ids"] = sorted(set(group["record_ids"]))
            group["relay_record_ids"] = sorted(set(group["relay_record_ids"]))
            group["direct_source_record_ids"] = sorted(set(group["direct_source_record_ids"]))
            group["authorization_ids"] = sorted(set(group["authorization_ids"]))
            group["is_repeated_relay"] = len(group["record_ids"]) > 1 or bool(group["relay_record_ids"])
            # One evidence root is never counted as several independent grants.
            group["independent_authorization_count"] = 1 if (
                group["direct_source_record_ids"] or group["authorization_ids"]
            ) else 0
        return result

    def extracted_authorizations(self, claims: list[Any]) -> list[Authorization]:
        """Turn direct permission notes into unverified authorization candidates.

        Hearsay/forwarded consent is intentionally not converted.
        """

        existing_evidence = {
            rid for a in self.case.authorizations for rid in a.evidence_ids
        }
        existing_evidence.update(
            a.root_evidence_id for a in self.case.authorizations if a.root_evidence_id
        )
        results: list[Authorization] = []
        for claim in claims:
            if claim.kind != "license_text" or claim.details.get("is_hearsay"):
                continue
            record_id = claim.source_record_ids[0] if claim.source_record_ids else claim.id
            if record_id in existing_evidence:
                continue
            person_ids = {rid for rid in claim.refs.get("mentioned_entity_ids", [])
                          if (e := self.case.entity(rid)) and e.type == "person"}
            character_ids = {rid for rid in claim.refs.get("mentioned_entity_ids", [])
                             if (e := self.case.entity(rid)) and e.type == "character"}
            work_ids = {rid for rid in claim.refs.get("mentioned_entity_ids", [])
                        if (e := self.case.entity(rid)) and e.type == "work"}
            if not (person_ids or character_ids or work_ids):
                # A rights sentence without any stable object remains a claim,
                # not an authorization candidate.
                continue
            text = claim.text
            rights: set[str] = set()
            if any(w in text for w in ("脸", "肖像", "形象")):
                rights.add("likeness")
            if any(w in text for w in ("饰演", "扮演", "出演", "表演", "演出")):
                rights.add("performance")
            if "角色" in text:
                rights.add("character_use")
            if any(w in text for w in ("作品", "发行", "公映", "上线")):
                rights.add("work_release")
            if not rights:
                rights.add("unspecified_license")
            auth = Authorization(
                id=f"AUTH-EXTRACTED-{record_id}",
                right_types=rights,
                person_ids=person_ids,
                character_ids=character_ids,
                work_ids=work_ids,
                subject_ids=person_ids | character_ids | work_ids,
                scope=claim.scope or Scope(),
                evidence_ids=[record_id],
                root_evidence_id=claim.root_evidence_id or record_id,
                verification_status=VerificationStatus.UNVERIFIED.value,
                note="由原始授权/许可文本保守抽取；仍需核验来源与权利人身份",
                metadata={"extracted": True},
            )
            results.append(auth)
            existing_evidence.add(record_id)
        return results

    def all_authorizations(self, claims: list[Any]) -> list[Authorization]:
        return list(self.case.authorizations) + self.extracted_authorizations(claims)

    def authorization_bases(self, auths: list[Authorization]) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for auth in sorted(auths, key=lambda a: a.id):
            key = self._auth_group_key(auth)
            group = groups.setdefault(key, {
                "basis_key": json.dumps(key, ensure_ascii=False, sort_keys=True, default=str),
                "root_authorization_id": auth.root_authorization_id,
                "authorization_ids": [],
                "evidence_ids": set(),
                "record_ids": set(),
                "duplicate_count": 0,
            })
            group["authorization_ids"].append(auth.id)
            group["duplicate_count"] += 1
            group["evidence_ids"].update(auth.evidence_ids)
            if auth.root_evidence_id:
                group["evidence_ids"].add(auth.root_evidence_id)
            for eid in auth.evidence_ids:
                group["record_ids"].add(eid)
        result = []
        for group in groups.values():
            group["authorization_ids"] = sorted(set(group["authorization_ids"]))
            group["evidence_ids"] = sorted(group["evidence_ids"])
            group["record_ids"] = sorted(group["record_ids"])
            group["is_duplicate_evidence"] = group["duplicate_count"] > 1 or len(group["authorization_ids"]) > 1
            group["independent_authorization_count"] = 1
            result.append(group)
        return sorted(result, key=lambda g: g["basis_key"])

    @staticmethod
    def _auth_group_key(auth: Authorization) -> tuple[Any, ...]:
        if auth.root_authorization_id:
            return ("root", auth.root_authorization_id)
        return (
            "content",
            auth.grantor_id,
            auth.rights_holder_id,
            tuple(sorted(auth.right_types)),
            tuple(sorted(auth.person_ids)),
            tuple(sorted(auth.character_ids)),
            tuple(sorted(auth.work_ids)),
            tuple(sorted(auth.subject_ids)),
            auth.scope.canonical_key(),
            auth.root_evidence_id,
        )

    # ------------------------------------------------------------------
    # Contacts / relations
    # ------------------------------------------------------------------
    def contact_reports(self) -> list[dict[str, Any]]:
        result = []
        by_rep: dict[str, list[Any]] = {}
        for contact in sorted(self.case.contacts, key=lambda c: c.id):
            if contact.represents_id:
                by_rep.setdefault(contact.represents_id, []).append(contact)
        for contact in sorted(self.case.contacts, key=lambda c: c.id):
            represented = self.case.entity(contact.represents_id) if contact.represents_id else None
            person = self.case.entity(contact.person_id) if contact.person_id else None
            same_family = by_rep.get(contact.represents_id or "", [])
            statuses = {
                "verified" if (c.authority_status in {"verified", "confirmed"} or c.verified is True)
                else c.authority_status
                for c in same_family
            }
            contact_verified = (
                contact.authority_status in {"verified", "confirmed"}
                or getattr(contact, "verified", None) is True
            )
            report = contact.to_dict()
            report.update({
                "represented_name": represented.name if represented else None,
                "person_name": person.name if person else contact.name or None,
                "multiple_relatives": len(same_family) > 1,
                "authority_conflict": (
                    VerificationStatus.VERIFIED.value in statuses
                    and VerificationStatus.REJECTED.value in statuses
                ) or VerificationStatus.CONFLICTED.value in statuses,
                "standing": (
                    "verified" if contact_verified
                    else "conflicted" if contact.authority_status == "conflicted"
                    else "pending_confirmation"
                ),
            })
            result.append(report)
        return result

    def relations(self, claims: list[Any]) -> list[dict[str, Any]]:
        relations: list[dict[str, Any]] = []
        seen: set[str] = set()
        semantic_seen: set[tuple[Any, ...]] = set()

        def add(rel_id: str, kind: str, from_id: str | None, to_id: str | None,
                status: str, evidence: list[str], description: str) -> None:
            key = rel_id or f"{kind}:{from_id}:{to_id}:{description}"
            if key in seen:
                return
            semantic_key = (kind, from_id, to_id, tuple(sorted(set(evidence or []))))
            if semantic_key in semantic_seen:
                return
            seen.add(key)
            semantic_seen.add(semantic_key)
            relations.append({
                "id": key,
                "kind": kind,
                "from_id": from_id,
                "to_id": to_id,
                "status": status,
                "evidence_ids": sorted(set(evidence)),
                "description": description,
            })

        for i, rel in enumerate(self.case.relations):
            rel_id = str(rel.get("id", f"REL-INPUT-{i + 1}"))
            add(
                rel_id, str(rel.get("kind", "related_to")),
                rel.get("from_id") or rel.get("source_id"),
                rel.get("to_id") or rel.get("target_id"),
                str(rel.get("status", "grounded")),
                list(rel.get("evidence_ids", rel.get("evidence", [])) or []),
                str(rel.get("description", "")),
            )

        for claim in claims:
            if claim.kind == "portrayal":
                p = claim.details.get("person_id")
                c = claim.details.get("character_id")
                add(
                    f"REL-PORTRAYAL-{p}-{c}", "portrayal", p, c, "grounded",
                    claim.source_record_ids,
                    "原始文本明确记载表演者与角色的饰演关系",
                )
            for mention in claim.details.get("mentions", []) or []:
                if mention.get("status") == "candidate_pending_confirmation" and mention.get("entity_id"):
                    record_id = claim.source_record_ids[0] if claim.source_record_ids else None
                    add(
                        f"REL-NAME-CANDIDATE-{claim.id}-{mention['entity_id']}",
                        "name_candidate", record_id, mention["entity_id"],
                        "pending_confirmation", claim.source_record_ids,
                        "称谓/姓氏只形成唯一候选，仍需稳定标识或权利人确认，不能直接替代本人",
                    )
                elif mention.get("status") == "ambiguous":
                    record_id = claim.source_record_ids[0] if claim.source_record_ids else None
                    for candidate_id in mention.get("candidate_ids", []) or []:
                        add(
                            f"REL-NAME-AMBIGUOUS-{claim.id}-{candidate_id}",
                            "ambiguous_name_candidate", record_id, candidate_id,
                            "pending_confirmation", claim.source_record_ids,
                            "存在多个同姓候选，不能按相似度或称谓自动合并",
                        )
            if claim.kind == "rename":
                d = claim.details
                source_id = claim.source_record_ids[0] if claim.source_record_ids else claim.id
                add(
                    f"REL-RENAME-{d.get('entity_id')}-{source_id}", "name_revision",
                    d.get("entity_id"), d.get("entity_id"),
                    "grounded", claim.source_record_ids,
                    f"角色名称由 {d.get('old_name')} 修订为 {d.get('new_name')}；稳定编号不变",
                )

        for rev in self.case.revisions:
            if rev.entity_id and rev.kind == "rename":
                add(
                    f"REL-REVISION-{rev.id}", "name_revision", rev.entity_id, rev.entity_id,
                    "grounded", [rev.source_record_id] if rev.source_record_id else [],
                    rev.note or f"名称由 {rev.old_value} 改为 {rev.new_value}",
                )
            elif rev.entity_id and rev.kind == "remove":
                add(
                    f"REL-REVISION-{rev.id}", "removal", rev.entity_id, None,
                    "grounded", [rev.source_record_id] if rev.source_record_id else [],
                    rev.note or "对象被撤下/停用，但记录与关系未删除",
                )

        for contact in self.case.contacts:
            if contact.represents_id:
                add(
                    f"REL-CONTACT-{contact.id}-{contact.represents_id}",
                    "rights_contact_for", contact.id, contact.represents_id,
                    "grounded" if contact.evidence_ids else "pending",
                    contact.evidence_ids,
                    "权利联系人/近亲属材料指向特定逝者或权利人；亲属身份不自动等于代理权",
                )

        for item in self.case.release_items:
            for req in item.requirements:
                if req.subject_id != "*":
                    add(
                        f"REL-REQUIRES-{item.id}-{req.id}", "requires_right",
                        item.id, req.subject_id, "grounded", [],
                        f"待发内容需要具体权利类型 {req.right_type}；不同对象不得互相替代",
                    )
        return sorted(relations, key=lambda r: r["id"])

    # ------------------------------------------------------------------
    # Authorization matching
    # ------------------------------------------------------------------
    def _contact_for_auth(self, auth: Authorization) -> Any | None:
        contact_id = auth.contact_id or auth.grantor_id
        if contact_id:
            contact = next((c for c in self.case.contacts if c.id == contact_id), None)
            if contact:
                return contact
            # grantor_id may be a person/entity id; find contact by person_id.
            return next((c for c in self.case.contacts if c.person_id == contact_id), None)
        return None

    def _objections_for(self, item: Any, req: Any | None, auth: Authorization | None = None) -> list[Any]:
        applicable: list[Any] = []
        item_refs = item.refs() if item else set()
        req_subject = {req.subject_id} if req and req.subject_id != "*" else set()
        for obj in self.case.objections:
            if obj.status != "open":
                continue
            contact = next((c for c in self.case.contacts if c.id == obj.contact_id), None)
            contact_refs = {obj.person_id, obj.contact_id}
            if contact:
                contact_refs.update({contact.person_id, contact.represents_id})
            target_entities = set(obj.target_entity_ids) | {x for x in contact_refs if x}
            applies = False
            if item and obj.target_item_ids & {item.id}:
                applies = True
            if req and target_entities & req_subject:
                applies = True
            if item and target_entities & item_refs:
                applies = True
            if auth and obj.target_authorization_ids & {auth.id, auth.root_authorization_id or ""}:
                applies = True
            if obj.target_authorization_ids and auth and auth.id in obj.target_authorization_ids:
                applies = True
            if applies:
                applicable.append(obj)
        return applicable

    def _evaluate_requirement(self, item: Any, req: Any, auths: list[Authorization]) -> dict[str, Any]:
        entity = self.case.entity(req.subject_id) if req.subject_id != "*" else None
        requested_scope = self._requested_scope(item)
        bases: list[dict[str, Any]] = []
        for auth in sorted(auths, key=lambda a: a.id):
            subject_match = self._subject_matches(auth, req)
            right_match = right_covers(auth.right_types, req.right_type)
            if not (subject_match and right_match):
                continue
            scope_match = auth.scope.allows(requested_scope)
            contact = self._contact_for_auth(auth)
            contact_verified = bool(
                contact and (
                    contact.authority_status in {"verified", "confirmed"}
                    or getattr(contact, "verified", None) is True
                )
            )
            contact_conflict = bool(contact and contact.authority_status == "conflicted")
            auth_verified = (
                auth.verification_status in {"verified", "confirmed", "clear", "cleared"}
                or getattr(auth, "verified", None) is True
            )
            authority_confirmed = auth_verified or contact_verified
            lifecycle_blocked = auth.lifecycle_status in {"revoked", "expired", "withdrawn", "rejected"}
            objections = self._objections_for(item, req, auth)
            usable = (
                authority_confirmed
                and not contact_conflict
                and not lifecycle_blocked
                and scope_match.ok
                and not objections
            )
            reasons: list[str] = []
            if not authority_confirmed:
                reasons.append("authorization_or_contact_not_verified")
            if contact_conflict:
                reasons.append("contact_authority_conflicted")
            if lifecycle_blocked:
                reasons.append(f"authorization_{auth.lifecycle_status}")
            if not scope_match.ok:
                reasons.extend(scope_match.reasons)
            if objections:
                reasons.append("open_rights_objection")
            bases.append({
                "authorization_id": auth.id,
                "root_authorization_id": auth.root_authorization_id,
                "subject_match": subject_match,
                "right_match": right_match,
                "scope_match": scope_match.to_dict(),
                "verification_status": auth.verification_status,
                "lifecycle_status": auth.lifecycle_status,
                "contact_id": contact.id if contact else auth.contact_id,
                "contact_authority_status": contact.authority_status if contact else None,
                "authority_confirmed": authority_confirmed,
                "objection_ids": [o.id for o in objections],
                "usable": usable,
                "reasons": sorted(set(reasons)),
                "evidence_ids": auth.evidence_ids,
            })

        removed = bool(entity and entity.status == "removed")
        any_usable = any(b["usable"] for b in bases)
        open_obj = self._objections_for(item, req)
        if removed:
            state = "subject_removed"
        elif any_usable:
            state = "grounded"
        elif open_obj:
            state = "objection_open"
        elif not bases:
            state = "missing_authorization"
        elif any(b["scope_match"]["ok"] and b["authority_confirmed"] for b in bases):
            state = "blocked"
        elif any(not b["scope_match"]["ok"] for b in bases) and any(b["authority_confirmed"] for b in bases):
            state = "scope_not_covered"
        else:
            state = "pending_confirmation"

        all_reasons = sorted({reason for b in bases for reason in b["reasons"]})
        if state == "grounded":
            # A narrower training-only candidate may still appear in bases,
            # but one usable release basis clears this requirement.  Keep the
            # candidate-specific explanation inside matching_bases.
            reasons: list[str] = []
        else:
            reasons = list(all_reasons)
            if removed:
                reasons.append("referenced_entity_removed")
            if open_obj and state != "objection_open":
                reasons.append("open_rights_objection")
        return {
            "requirement_id": req.id,
            "right_type": req.right_type,
            "subject_type": req.subject_type,
            "subject_id": req.subject_id,
            "subject_name": entity.name if entity else None,
            "subject_status": entity.status if entity else None,
            "state": state,
            "state_label": STATE_LABELS.get(state, state),
            "is_satisfied": state == "grounded",
            "matching_bases": bases,
            "objection_ids": [o.id for o in open_obj],
            "reasons": reasons,
            "note": req.note,
        }

    @staticmethod
    def _requested_scope(item: Any) -> Scope:
        scope = Scope.from_dict(item.scope.to_dict() if isinstance(item.scope, Scope) else item.scope)
        # An empty release-item request is interpreted conservatively as asking
        # for publication.  An explicit narrow request such as a closed school
        # screening is evaluated as written instead of silently adding a broader
        # public-release purpose.
        has_dimension = bool(scope.purposes or scope.channels or scope.uses)
        if not has_dimension:
            scope.purposes.add("release")
        return scope

    @staticmethod
    def _subject_matches(auth: Authorization, req: Any) -> bool:
        if req.subject_id == "*":
            return True
        exact_sets = {
            "person": auth.person_ids,
            "character": auth.character_ids,
            "work": auth.work_ids,
        }
        # Exact stable ID in a typed set is valid.
        if req.subject_type in exact_sets and req.subject_id in exact_sets[req.subject_type]:
            return True
        # Generic subject list is still an exact ID link, never a name guess.
        return req.subject_id in auth.subject_ids

    # ------------------------------------------------------------------
    # Item review
    # ------------------------------------------------------------------
    def _review_item(self, item: Any, auths: list[Authorization], claims: list[Any]) -> dict[str, Any]:
        req_reports = [self._evaluate_requirement(item, req, auths) for req in item.requirements]
        note_findings = self._note_findings(item)
        blockers: list[str] = []
        if not req_reports:
            blockers.append("no_rights_subjects_specified")
        if any(not r["is_satisfied"] for r in req_reports):
            blockers.append("unresolved_rights_requirement")
        if note_findings:
            blockers.append("generic_consent_note_is_not_authorization")
        if any(r["state"] == "subject_removed" for r in req_reports):
            blockers.append("affected_subject_removed")
        objection_ids = sorted({oid for r in req_reports for oid in r["objection_ids"]})
        if objection_ids:
            blockers.append("open_rights_objection")

        ready = not blockers and all(r["is_satisfied"] for r in req_reports)
        decision = "ready_for_release" if ready else "hold_for_confirmation"
        reasons = sorted(set(blockers + [reason for r in req_reports for reason in r["reasons"]]))
        return {
            "item_id": item.id,
            "title": item.title,
            "revision": item.revision,
            "status": item.status,
            "decision": decision,
            "decision_label": DECISION_LABELS.get(decision, decision),
            "clearance": decision,
            "ready": ready,
            "can_release": ready,
            "can_publish": ready,
            "reasons": reasons,
            "requirement_reviews": req_reports,
            "note_findings": note_findings,
            "objection_ids": objection_ids,
            "refs": sorted(item.refs()),
            "requested_scope": self._requested_scope(item).to_dict(),
        }

    @staticmethod
    def _note_findings(item: Any) -> list[dict[str, Any]]:
        findings = []
        note_texts = list(item.notes)
        # Notes may be raw record IDs or actual strings.
        for rid in item.note_record_ids:
            # The engine caller passes case; this static helper cannot resolve.
            # Resolution is done in _attach_note_records below.
            note_texts.append(f"record:{rid}")
        for i, note in enumerate(note_texts):
            if note.startswith("record:"):
                continue
            if has_generic_consent(note):
                findings.append({
                    "finding_id": f"NOTE-{item.id}-{i + 1}",
                    "code": "GENERIC_CONSENT_NOT_SUFFICIENT",
                    "severity": "blocker",
                    "message": "备注中的“已获同意”没有权利主体、对象、权利类型和范围，不能作为发布授权。",
                    "excerpt": note,
                })
        return findings

    def _attach_note_records(self, item_reviews: list[dict[str, Any]]) -> None:
        by_id = {r["item_id"]: r for r in item_reviews}
        for item in self.case.release_items:
            report = by_id[item.id]
            existing_ids = {f["finding_id"] for f in report["note_findings"]}
            for rid in item.note_record_ids:
                record = self.case.record(rid)
                if not record:
                    continue
                text = record.text
                if has_generic_consent(text):
                    fid = f"NOTE-{item.id}-REC-{rid}"
                    if fid in existing_ids:
                        continue
                    report["note_findings"].append({
                        "finding_id": fid,
                        "code": "GENERIC_CONSENT_NOT_SUFFICIENT",
                        "severity": "blocker",
                        "message": "关联备注/记录只有概括同意主张，不能替代可核验授权。",
                        "evidence_ids": [rid],
                        "excerpt": text,
                    })
                    if "generic_consent_note_is_not_authorization" not in report["reasons"]:
                        report["reasons"].append("generic_consent_note_is_not_authorization")
                    report["decision"] = "hold_for_confirmation"
                    report["decision_label"] = DECISION_LABELS["hold_for_confirmation"]
                    report["clearance"] = "hold_for_confirmation"
                    report["ready"] = False
                    report["can_release"] = False
                    report["can_publish"] = False

    # ------------------------------------------------------------------
    # Matters
    # ------------------------------------------------------------------
    def _reconcile_matters(self, item_reviews: list[dict[str, Any]], claims: list[Any]) -> list[dict[str, Any]]:
        issues: dict[str, dict[str, Any]] = {}

        def issue(matter_id: str, title: str, code: str, *, severity: str = "blocker",
                  refs: dict[str, list[str]] | None = None,
                  snapshot: dict[str, Any] | None = None) -> None:
            entry = issues.setdefault(matter_id, {
                "id": matter_id, "title": title, "codes": [], "severity": severity,
                "refs": refs or {}, "snapshot": snapshot or {},
            })
            if code not in entry["codes"]:
                entry["codes"].append(code)

        for review in item_reviews:
            for req in review["requirement_reviews"]:
                if req["is_satisfied"]:
                    continue
                mid = f"MAT-{req['requirement_id']}"
                names = req.get("subject_name") or req["subject_id"]
                issue(
                    mid,
                    f"待确认权利：{review['item_id']} / {req['right_type']} / {names}",
                    req["state"].upper(),
                    refs={"items": [review["item_id"]],
                          "entities": [req["subject_id"]] if req["subject_id"] != "*" else []},
                    snapshot={
                        "item_id": review["item_id"],
                        "right_type": req["right_type"],
                        "subject_id": req["subject_id"],
                        "subject_name_at_first_review": req.get("subject_name"),
                        "revision": review.get("revision"),
                    },
                )
            for finding in review["note_findings"]:
                issue(
                    f"MAT-{finding['finding_id']}",
                    f"概括同意备注不能放行：{review['item_id']}",
                    finding["code"],
                    refs={"items": [review["item_id"]]},
                    snapshot={"excerpt": finding.get("excerpt")},
                )

        for claim in claims:
            if claim.kind == "generic_consent_note":
                issue(
                    f"MAT-{claim.id}",
                    f"概括同意主张待权利人确认：{claim.id}",
                    "GENERIC_CONSENT_CLAIM",
                    severity="warning",
                    refs={
                        "records": claim.source_record_ids,
                        "entities": claim.refs.get("mentioned_entity_ids", []),
                    },
                    snapshot={"text": claim.text},
                )
            elif claim.kind == "objection_text":
                issue(
                    f"MAT-{claim.id}",
                    f"原始异议/否认主张待核验联系人地位：{claim.id}",
                    "OBJECTION_CLAIM_PENDING_STANDING",
                    severity="blocker",
                    refs={
                        "records": claim.source_record_ids,
                        "entities": claim.refs.get("mentioned_entity_ids", []),
                    },
                    snapshot={"text": claim.text},
                )

        for obj in self.case.objections:
            if obj.status != "open":
                continue
            issue(
                f"MAT-{obj.id}",
                f"权利联系人异议待处理：{obj.id}",
                "OBJECTION_OPEN",
                refs={
                    "objections": [obj.id],
                    "items": sorted(obj.target_item_ids),
                    "entities": sorted(obj.target_entity_ids),
                    "authorizations": sorted(obj.target_authorization_ids),
                },
                snapshot={"reason": obj.reason, "record_id": obj.record_id},
            )

        existing = {m.id: m for m in self.case.matters}
        for mid, data in issues.items():
            if mid not in existing:
                matter = Matter(
                    id=mid,
                    title=data["title"],
                    kind="rights_confirmation",
                    status="open",
                    severity=data["severity"],
                    codes=sorted(data["codes"]),
                    refs=data["refs"],
                    opened_source="review",
                    subject_snapshot=data["snapshot"],
                )
                matter.add_event("opened", "首次复核发现待处理事项", source="review-engine")
                self.case.matters.append(matter)
                existing[mid] = matter
            else:
                # Do not overwrite title/snapshot/status.  New codes are merged
                # only into a non-destructive code list for indexing.
                for code in data["codes"]:
                    if code not in existing[mid].codes:
                        existing[mid].codes.append(code)

        report = []
        for matter in sorted(self.case.matters, key=lambda m: m.id):
            live_issue = issues.get(matter.id)
            current = matter.to_dict()
            current["live_issue"] = live_issue
            current["still_detected_by_rules"] = live_issue is not None
            current["manual_closure_required"] = matter.status != "resolved" and live_issue is None
            current["silently_rewritten"] = False
            report.append(current)
        return report

    # ------------------------------------------------------------------
    # Revision impact / history
    # ------------------------------------------------------------------
    def _impacts(self) -> list[dict[str, Any]]:
        impacts = []
        archived_reports = self.case.review_history
        for rev in sorted(self.case.revisions, key=lambda r: (r.sequence, r.id)):
            entity_id = rev.entity_id
            item_id = rev.item_id
            affected_items: set[str] = set()
            affected_versions: list[dict[str, Any]] = []
            affected_matters: set[str] = set()
            affected_publications: set[str] = set()

            for item in self.case.release_items:
                item_blob = json.dumps(item.to_dict(), ensure_ascii=False, default=str)
                current_hit = (item_id == item.id) or (entity_id and entity_id in item.refs())
                history_hit = bool(entity_id and entity_id in item_blob and not current_hit)
                if current_hit or history_hit or item_id == item.id:
                    affected_items.add(item.id)
                if entity_id and entity_id in json.dumps(item.history, ensure_ascii=False, default=str):
                    affected_versions.append({"item_id": item.id, "history": item.history})
                elif item_id == item.id and item.history:
                    affected_versions.append({"item_id": item.id, "history": item.history})

            for matter in self.case.matters:
                blob = json.dumps(matter.to_dict(), ensure_ascii=False, default=str)
                if (entity_id and entity_id in blob) or (item_id and item_id in blob):
                    affected_matters.add(matter.id)

            for pub in self.case.publications:
                if pub.item_id in affected_items or (item_id and pub.item_id == item_id):
                    affected_publications.add(pub.id)

            prior_reviews = []
            for archived in archived_reports:
                review_id = archived.get("review_id") or archived.get("id") or "archived-review"
                blob = json.dumps(archived, ensure_ascii=False, default=str)
                if (entity_id and entity_id in blob) or (item_id and item_id in blob):
                    prior_reviews.append(review_id)

            impacts.append({
                "revision": rev.to_dict(),
                "affected_item_ids": sorted(affected_items),
                "affected_item_versions": affected_versions,
                "affected_matter_ids": sorted(affected_matters),
                "affected_publication_ids": sorted(affected_publications),
                "prior_review_ids": sorted(set(prior_reviews)),
                "judgments_preserved": True,
                "note": "修订只增加新版本/影响视图；原名称、原判断、原事项和已发生发布记录均不删除或静默改写。",
            })
        return impacts

    def _publication_history(self) -> list[dict[str, Any]]:
        result = []
        items = {item.id: item for item in self.case.release_items}
        for pub in sorted(self.case.publications, key=lambda p: (p.sequence, p.id)):
            data = pub.to_dict()
            data["record_retained_after_objection"] = True
            item = items.get(pub.item_id)
            item_refs = item.refs() if item else set()
            related_objections = []
            timing = []
            for obj in sorted(self.case.objections, key=lambda o: o.id):
                contact = next((c for c in self.case.contacts if c.id == obj.contact_id), None)
                entity_refs = set(obj.target_entity_ids)
                if contact:
                    entity_refs.update(x for x in (contact.person_id, contact.represents_id) if x)
                applies = (
                    pub.item_id in obj.target_item_ids
                    or bool(entity_refs & item_refs)
                )
                if not applies:
                    continue
                related_objections.append(obj.id)
                before: bool | None = None
                if pub.sequence and obj.sequence and pub.sequence != obj.sequence:
                    before = pub.sequence < obj.sequence
                elif pub.published_at and obj.received_at:
                    before = str(pub.published_at) < str(obj.received_at)
                timing.append({
                    "objection_id": obj.id,
                    "publication_before_objection": before,
                    "objection_status": obj.status,
                })
            data["objection_ids"] = sorted(set(related_objections))
            data["objection_timing"] = timing
            result.append(data)
        return result

    # ------------------------------------------------------------------
    # Main review
    # ------------------------------------------------------------------
    def review(self, review_id: str | None = None, *, archive: bool = False) -> ReviewReport:
        claims = self.all_claims()
        auths = self.all_authorizations(claims)
        item_reviews = [self._review_item(item, auths, claims) for item in self.case.release_items]
        self._attach_note_records(item_reviews)
        matters = self._reconcile_matters(item_reviews, claims)
        impacts = self._impacts()
        auth_bases = self.authorization_bases(auths)

        ready = sum(1 for r in item_reviews if r["decision"] == "ready_for_release")
        hold = len(item_reviews) - ready
        excluded_inputs = dict(self.case.metadata.get("excluded_signals", {}))
        for signal_key in ("similarity", "similarity_score", "similarity_scores",
                           "public_sentiment", "heat", "heat_score", "trending"):
            if signal_key in self.case.metadata and signal_key not in excluded_inputs:
                excluded_inputs[signal_key] = self.case.metadata[signal_key]
        payload: dict[str, Any] = {
            "schema_version": "reviewdesk.report/v1",
            "review_id": review_id or "REVIEW-CURRENT",
            "case_id": self.case.case_id,
            "policy": {
                "entity_types_are_distinct": True,
                "names_are_not_authorization_substitutes": True,
                "generic_consent_note_is_not_authorization": True,
                "repeated_evidence_counts_once": True,
                "publication_history_is_append_only": True,
                "revisions_preserve_prior_judgments": True,
                "automatic_infringement_from_similarity_or_heat": False,
                "infringement_conclusion": "not_determined",
            },
            "decision_summary": {
                "total_items": len(item_reviews),
                "ready_for_release": ready,
                "hold_for_confirmation": hold,
                "open_matters": sum(1 for m in self.case.matters if m.status != "resolved"),
            },
            "decisions": {r["item_id"]: {
                "decision": r["decision"], "ready": r["ready"], "reasons": r["reasons"]
            } for r in item_reviews},
            "entities": [self._entity_report(e) for e in sorted(self.case.entities, key=lambda x: x.id)],
            "records": [r.to_dict() for r in sorted(self.case.records, key=lambda r: r.id)],
            "raw_records": [r.to_dict() for r in sorted(self.case.records, key=lambda r: r.id)],
            "relations": self.relations(claims),
            "claims": [c.to_dict() for c in claims],
            "evidence_groups": self.evidence_groups(claims, auths),
            "authorization_bases": auth_bases,
            "independent_authorization_count": len(auth_bases),
            "authorizations": [a.to_dict() for a in sorted(auths, key=lambda a: a.id)],
            "contacts": self.contact_reports(),
            "items": item_reviews,
            "release_reviews": item_reviews,
            "reviews": item_reviews,
            "matters": matters,
            "revisions": [r.to_dict() for r in sorted(self.case.revisions, key=lambda r: (r.sequence, r.id))],
            "impacts": impacts,
            "objections": [o.to_dict() for o in sorted(self.case.objections, key=lambda o: o.id)],
            "publication_history": self._publication_history(),
            "infringement": assess_infringement(
                similarity=excluded_inputs.get("similarity", excluded_inputs.get("similarity_score")),
                similarity_score=excluded_inputs.get("similarity_scores"),
                public_sentiment=excluded_inputs.get("public_sentiment"),
                heat=excluded_inputs.get("heat", excluded_inputs.get("heat_score")),
                trending=excluded_inputs.get("trending"),
            ).to_dict(),
            "excluded_signals": excluded_inputs,
        }
        payload["grounded_relations"] = [
            r for r in payload["relations"] if r.get("status") == "grounded"
        ]
        payload["pending_relations"] = [
            r for r in payload["relations"] if r.get("status") != "grounded"
        ]
        payload["pending_confirmations"] = [
            {
                "item_id": item["item_id"],
                "requirement_id": req["requirement_id"],
                "right_type": req["right_type"],
                "subject_id": req["subject_id"],
                "state": req["state"],
                "reasons": req["reasons"],
            }
            for item in item_reviews
            for req in item["requirement_reviews"]
            if not req["is_satisfied"]
        ]
        payload["open_matter_ids"] = [matter.id for matter in self.case.matters if matter.status != "resolved"]
        payload["all_items_ready"] = bool(item_reviews) and all(i["ready"] for i in item_reviews)
        payload["can_release_all"] = payload["all_items_ready"]
        report = ReviewReport(payload)
        if archive:
            self.case.review_history.append(payload)
        return report

    @staticmethod
    def _entity_report(entity: Any) -> dict[str, Any]:
        data = entity.to_dict() if hasattr(entity, "to_dict") else to_jsonable(entity)
        data["all_names"] = entity.all_names()
        data["current_name"] = entity.name
        former_names = [name for name in entity.aliases if name != entity.name]
        data["former_names"] = list(dict.fromkeys(former_names))
        return data


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# 发布复核报告：{report['case_id']}")
    lines.append("")
    summary = report["decision_summary"]
    lines.append(
        f"待发内容 {summary['total_items']}；可放行 {summary['ready_for_release']}；"
        f"暂缓/待确认 {summary['hold_for_confirmation']}；未结事项 {summary['open_matters']}。"
    )
    lines.append("")
    lines.append("## 发布决定")
    if not report["items"]:
        lines.append("- 本卷没有结构化待发内容；以下仅列原始主张与证据。")
    for item in report["items"]:
        mark = "✅ 可放行" if item["ready"] else "⛔ 暂缓"
        lines.append(f"- {mark} `{item['item_id']}` {item['title']}（{item['revision']}）")
        if item["reasons"]:
            lines.append(f"  - 原因：{', '.join(item['reasons'])}")
        for req in item["requirement_reviews"]:
            icon = "✔" if req["is_satisfied"] else "•"
            name = req.get("subject_name") or req["subject_id"]
            lines.append(f"  - {icon} {req['right_type']} → {name} [`{req['subject_id']}`]：{req['state']}")
            for basis in req["matching_bases"]:
                scope = basis["scope_match"]
                lines.append(
                    f"    - 依据 `{basis['authorization_id']}`；主体/权利匹配，"
                    f"核验={basis['authority_confirmed']}，范围可用={scope['ok']}，"
                    f"可用={basis['usable']}"
                )
                if scope.get("missing"):
                    lines.append(f"      - 缺少范围：{json.dumps(scope['missing'], ensure_ascii=False)}")
                if scope.get("denied_hits"):
                    lines.append(f"      - 命中排除范围：{json.dumps(scope['denied_hits'], ensure_ascii=False)}")
        for finding in item.get("note_findings", []):
            lines.append(f"  - ⚠ {finding['message']}")
    lines.append("")

    lines.append("## 关系依据")
    for rel in report.get("relations", []):
        status = "有依据" if rel["status"] == "grounded" else "待确认"
        lines.append(
            f"- [{status}] `{rel['kind']}` {rel.get('from_id')} → {rel.get('to_id')}"
            f"（证据：{', '.join(rel.get('evidence_ids', [])) or '无'}）：{rel.get('description', '')}"
        )
    if not report.get("relations"):
        lines.append("- 暂无可关联关系。")
    lines.append("")

    lines.append("## 原始主张与证据")
    for claim in report["claims"]:
        hearsay = "转述/传闻" if claim["hearsay"] else "原始表述"
        lines.append(
            f"- `{claim['id']}` [{claim['kind']}/{claim['status']}/{hearsay}] "
            f"证据：{', '.join(claim['source_record_ids'])}"
        )
    lines.append("")
    lines.append("### 证据去重组")
    for group in report["evidence_groups"]:
        repeat = "；多次转述不另计授权" if group["is_repeated_relay"] else ""
        records = ", ".join(group["record_ids"]) if group["record_ids"] else "（无原始记录编号）"
        auth_ids = ", ".join(group.get("authorization_ids", []))
        auth_text = f"；授权对象 {auth_ids}" if auth_ids else ""
        lines.append(
            f"- 根证据 `{group['root_evidence_id']}`：记录 {records}{auth_text}{repeat}"
        )
    lines.append("")

    lines.append("## 人物与权利联系人")
    if not report["contacts"]:
        lines.append("- 暂无结构化权利联系人。")
    for contact in report["contacts"]:
        extra = []
        if contact.get("multiple_relatives"):
            extra.append("有多名近亲属/联系人，材料分别保留")
        if contact.get("authority_conflict"):
            extra.append("代理权存在冲突，需核实")
        lines.append(
            f"- `{contact['id']}` 代表 `{contact.get('represents_id')}`；"
            f"关系：{contact.get('relationship') or '未填写'}；"
            f"地位：{contact['standing']}"
            + (f"；{'；'.join(extra)}" if extra else "")
        )
    lines.append("")

    lines.append("## 修订影响")
    if not report["impacts"]:
        lines.append("- 暂无修订事件。")
    for impact in report["impacts"]:
        rev = impact["revision"]
        lines.append(
            f"- `{rev['id']}` {rev['kind']}：{rev.get('old_value')} → {rev.get('new_value')}"
        )
        lines.append(
            f"  - 受影响待发内容：{', '.join(impact['affected_item_ids']) or '无'}；"
            f"既有事项：{', '.join(impact['affected_matter_ids']) or '无'}；"
            f"发布记录：{', '.join(impact['affected_publication_ids']) or '无'}"
        )
        lines.append(f"  - {impact['note']}")
    lines.append("")

    lines.append("## 既有事项（不会被静默关闭或改写）")
    if not report["matters"]:
        lines.append("- 无。")
    for matter in report["matters"]:
        live = "规则仍检出" if matter["still_detected_by_rules"] else (
            "原情形未再检出，需人工决定是否关闭" if matter["manual_closure_required"] else "已关闭"
        )
        lines.append(f"- `{matter['id']}` [{matter['status']}/{live}] {matter['title']}")
    lines.append("")

    lines.append("## 已发生发布记录")
    if not report["publication_history"]:
        lines.append("- 无已登记发布。")
    for pub in report["publication_history"]:
        lines.append(
            f"- `{pub['id']}` 内容 `{pub['item_id']}`；时间 {pub.get('published_at') or '未填写'}；"
            f"异议 {', '.join(pub.get('objection_ids', [])) or '无'}；记录保留，不因异议删除。"
        )
    lines.append("")

    inf = report["infringement"]
    lines.append("## 侵权判断边界")
    lines.append(f"- 结论：`{inf['conclusion']}`。{inf['message']}")
    if report.get("excluded_signals"):
        lines.append(f"- 未作为依据的信号：{', '.join(report['excluded_signals'].keys())}")
    lines.append("")
    return "\n".join(lines)
