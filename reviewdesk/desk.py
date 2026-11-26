"""Stateful, repeatable review desk with append-only audit operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .engine import ReviewEngine
from .ingest import extract_rename_notice, is_hearsay
from .model import (
    Authorization,
    Case,
    Contact,
    Objection,
    PublicationRecord,
    ReleaseItem,
    Revision,
    RightRequirement,
    Scope,
    _default_requirements,
)


class ReviewDesk(ReviewEngine):
    def __init__(self, case: Case | dict[str, Any] | str | Path | None = None):
        super().__init__(case if case is not None else Case())

    # ------------------------------------------------------------------
    # Loading / serialization
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, intake_text_revisions: bool = True) -> "ReviewDesk":
        case = Case.from_dict(payload)
        desk = cls(case)
        desk.apply_structured_revisions()
        if intake_text_revisions:
            desk.intake_text_revisions()
        return desk

    @classmethod
    def from_file(cls, path: str | Path, *, intake_text_revisions: bool = True) -> "ReviewDesk":
        payload = _read_json(path)
        return cls.from_dict(payload, intake_text_revisions=intake_text_revisions)

    @classmethod
    def load(cls, path: str | Path, *, intake_text_revisions: bool = True) -> "ReviewDesk":
        return cls.from_file(path, intake_text_revisions=intake_text_revisions)

    @classmethod
    def from_json(cls, path: str | Path, *, intake_text_revisions: bool = True) -> "ReviewDesk":
        return cls.from_file(path, intake_text_revisions=intake_text_revisions)

    def to_dict(self) -> dict[str, Any]:
        return self.case.to_dict()

    def save_json(self, path: str | Path) -> None:
        self.case.write_json(path)

    # ------------------------------------------------------------------
    # Generic additions
    # ------------------------------------------------------------------
    def add_record(self, record: Any) -> None:
        if isinstance(record, dict):
            from .model import RawRecord
            record = RawRecord.from_dict(record, len(self.case.records))
        self.case.records.append(record)

    def add_entity(self, entity: Any) -> None:
        if isinstance(entity, dict):
            from .model import Entity
            entity = Entity.from_dict(entity)
        if self.case.entity(entity.id):
            raise ValueError(f"entity already exists: {entity.id}")
        self.case.entities.append(entity)

    def add_authorization(self, auth: Any) -> Authorization:
        if isinstance(auth, dict):
            auth = Authorization.from_dict(auth, len(self.case.authorizations))
        self.case.authorizations.append(auth)
        return auth

    def add_contact(self, contact: Any) -> Contact:
        if isinstance(contact, dict):
            contact = Contact.from_dict(contact, len(self.case.contacts))
        self.case.contacts.append(contact)
        return contact

    def add_release_item(self, item: Any) -> ReleaseItem:
        if isinstance(item, dict):
            item = ReleaseItem.from_dict(item, len(self.case.release_items))
        if next((x for x in self.case.release_items if x.id == item.id), None):
            raise ValueError(f"release item already exists: {item.id}")
        self.case.release_items.append(item)
        return item

    # ------------------------------------------------------------------
    # Sequence / revisions
    # ------------------------------------------------------------------
    def _next_sequence(self) -> int:
        seq = [r.sequence for r in self.case.revisions if r.sequence]
        seq += [o.sequence for o in self.case.objections if o.sequence]
        seq += [p.sequence for p in self.case.publications if p.sequence]
        return (max(seq) + 1) if seq else 1

    def _revision_for_source(self, source_record_id: str | None, kind: str, entity_id: str | None) -> Revision | None:
        if not source_record_id:
            return None
        return next((
            r for r in self.case.revisions
            if r.source_record_id == source_record_id and r.kind == kind
            and (entity_id is None or r.entity_id == entity_id)
        ), None)

    def apply_structured_revisions(self) -> None:
        for rev in sorted(self.case.revisions, key=lambda r: (r.sequence, r.id)):
            if not rev.entity_id:
                continue
            entity = self.case.entity(rev.entity_id)
            if not entity:
                continue
            if rev.kind == "rename" and rev.new_value:
                new_name = rev.new_value if isinstance(rev.new_value, str) else rev.new_value.get("name")
                old_name = rev.old_value if isinstance(rev.old_value, str) else (
                    (rev.old_value or {}).get("name") if isinstance(rev.old_value, dict) else entity.name
                )
                if old_name and old_name != entity.name and old_name not in entity.aliases:
                    entity.aliases.append(old_name)
                if new_name and entity.name != new_name:
                    if entity.name not in entity.aliases:
                        entity.aliases.append(entity.name)
                    entity.name = new_name
            elif rev.kind in {"remove", "remove_work", "withdraw"}:
                entity.status = "removed"

    def intake_text_revisions(self) -> list[Revision]:
        """Apply explicit edit-notice rename/removal instructions.

        The raw record remains untouched and a revision event is appended.
        """

        applied: list[Revision] = []
        edit_kind_tokens = ("edit", "revision", "rename", "correction", "edit-notice",
                            "编辑", "修订", "改名", "更正", "撤下通知", "下线通知")
        completed_removal_markers = (
            "编辑撤下", "制片撤下", "已撤下", "现已撤下", "已经撤下",
            "决定撤下", "撤下通知", "已下线", "现已下线", "已移除",
        )
        request_markers = (
            "请撤下", "要求撤下", "请求撤下", "希望撤下", "请下线",
            "要求下线", "异议", "反对", "不同意", "暂停使用", "请暂停",
        )
        for record in sorted(self.case.records, key=lambda r: r.id):
            text = record.text or ""
            kind = (record.kind or "").lower()
            hearsay = is_hearsay(text, record.kind)
            is_edit_kind = any(token in kind for token in edit_kind_tokens)
            has_editorial_rename_marker = any(marker in text for marker in (
                "制片修订", "编辑修订", "修订：", "修订:", "编辑通知", "改名通知",
            ))

            rename = extract_rename_notice(record, self.case.entities)
            if (
                rename
                and not hearsay
                and (is_edit_kind or has_editorial_rename_marker)
                and not self._revision_for_source(record.id, "rename", rename["entity_id"])
            ):
                rev = self.rename_entity(
                    rename["entity_id"], rename["new_name"],
                    source_record_id=record.id,
                    note=f"根据编辑通知将 {rename['old_name']} 改名为 {rename['new_name']}；稳定编号不变",
                )
                applied.append(rev)

            is_completed_removal = any(marker in text for marker in completed_removal_markers)
            is_request = any(marker in text for marker in request_markers)
            if (
                not hearsay
                and not is_request
                and (is_completed_removal or (is_edit_kind and any(w in text for w in ("撤下", "下线", "移除"))))
            ):
                for entity in self.case.entities:
                    if entity.type != "work":
                        continue
                    if entity.name not in text:
                        continue
                    if self._revision_for_source(record.id, "remove", entity.id):
                        continue
                    if any(word in text for word in ("不撤下", "未撤下", "没有撤下", "暂不撤下")):
                        continue
                    rev = self.remove_entity(
                        entity.id, source_record_id=record.id,
                        note="根据已完成的编辑/撤下通知标记作品撤下；原始资料与事项保留",
                    )
                    applied.append(rev)
        return applied

    apply_text_revisions = intake_text_revisions
    apply_edit_notices = intake_text_revisions

    def rename_entity(
        self,
        entity_id: str,
        new_name: str,
        *,
        revision_id: str | None = None,
        source_record_id: str | None = None,
        at: str | None = None,
        note: str = "",
    ) -> Revision:
        entity = self.case.entity(entity_id)
        if entity is None:
            raise KeyError(f"unknown entity: {entity_id}")
        existing = self._revision_for_source(source_record_id, "rename", entity_id)
        if existing:
            return existing
        old_name = entity.name
        if old_name != new_name and old_name not in entity.aliases:
            entity.aliases.append(old_name)
        entity.name = new_name
        rev = Revision(
            id=revision_id or f"REV-{len(self.case.revisions) + 1}",
            kind="rename",
            sequence=self._next_sequence(),
            entity_id=entity_id,
            old_value=old_name,
            new_value=new_name,
            source_record_id=source_record_id,
            at=at,
            note=note or f"名称由 {old_name} 修订为 {new_name}",
        )
        self.case.revisions.append(rev)
        return rev

    def add_alias(self, entity_id: str, alias: str, *, source_record_id: str | None = None) -> Revision:
        entity = self.case.entity(entity_id)
        if entity is None:
            raise KeyError(entity_id)
        if alias not in entity.aliases and alias != entity.name:
            entity.aliases.append(alias)
        rev = Revision(
            id=f"REV-{len(self.case.revisions) + 1}", kind="add_alias",
            sequence=self._next_sequence(), entity_id=entity_id,
            old_value=None, new_value=alias, source_record_id=source_record_id,
            note=f"增加稳定别名/称谓 {alias}，不据此合并其他实体",
        )
        self.case.revisions.append(rev)
        return rev

    def remove_entity(
        self,
        entity_id: str,
        *,
        revision_id: str | None = None,
        source_record_id: str | None = None,
        at: str | None = None,
        note: str = "",
    ) -> Revision:
        entity = self.case.entity(entity_id)
        if entity is None:
            raise KeyError(f"unknown entity: {entity_id}")
        existing = self._revision_for_source(source_record_id, "remove", entity_id)
        if existing:
            return existing
        old_status = entity.status
        entity.status = "removed"
        rev = Revision(
            id=revision_id or f"REV-{len(self.case.revisions) + 1}",
            kind="remove",
            sequence=self._next_sequence(),
            entity_id=entity_id,
            old_value=old_status,
            new_value="removed",
            source_record_id=source_record_id,
            at=at,
            note=note or "对象被撤下/移除；不删除实体、关系、事项或发布历史",
        )
        self.case.revisions.append(rev)
        return rev

    def remove_work(self, work_id: str, **kwargs: Any) -> Revision:
        return self.remove_entity(work_id, **kwargs)

    rename_character = rename_entity
    rename = rename_entity
    remove = remove_entity

    # ------------------------------------------------------------------
    # Item revision
    # ------------------------------------------------------------------
    def revise_release_item(
        self,
        item_id: str,
        *,
        new_revision: str | None = None,
        revision: str | None = None,
        title: str | None = None,
        status: str | None = None,
        person_ids: Iterable[str] | None = None,
        character_ids: Iterable[str] | None = None,
        work_ids: Iterable[str] | None = None,
        scope: Scope | dict[str, Any] | None = None,
        notes: Iterable[str] | None = None,
        source_record_id: str | None = None,
        note: str = "",
    ) -> Revision:
        item = next((x for x in self.case.release_items if x.id == item_id), None)
        if item is None:
            raise KeyError(f"unknown release item: {item_id}")

        snapshot = {
            "revision": item.revision,
            "title": item.title,
            "status": item.status,
            "person_ids": sorted(item.person_ids),
            "character_ids": sorted(item.character_ids),
            "work_ids": sorted(item.work_ids),
            "requirements": [r.to_dict() for r in item.requirements],
            "scope": Scope.from_dict(item.scope).to_dict(),
            "notes": list(item.notes),
            "note_record_ids": list(item.note_record_ids),
        }
        item.history.append(snapshot)

        old_refs = item.refs()
        old_person_ids = set(item.person_ids)
        old_character_ids = set(item.character_ids)
        old_work_ids = set(item.work_ids)
        if new_revision or revision:
            item.revision = new_revision or revision or item.revision
        if title is not None:
            item.title = title
        if status is not None:
            item.status = status
        if person_ids is not None:
            item.person_ids = {str(x) for x in person_ids}
        if character_ids is not None:
            item.character_ids = {str(x) for x in character_ids}
        if work_ids is not None:
            item.work_ids = {str(x) for x in work_ids}
        if scope is not None:
            item.scope = scope if isinstance(scope, Scope) else Scope.from_dict(scope)
        if notes is not None:
            item.notes = [str(x) for x in notes]

        removed_refs: set[str] = set()
        if person_ids is not None:
            removed_refs |= old_person_ids - set(item.person_ids)
        if character_ids is not None:
            removed_refs |= old_character_ids - set(item.character_ids)
        if work_ids is not None:
            removed_refs |= old_work_ids - set(item.work_ids)

        # Drop only requirements for references explicitly removed by this
        # revision.  Custom/wildcard requirements and unchanged subjects are
        # retained.  Default requirements are added for newly linked subjects.
        item.requirements = [
            req for req in item.requirements
            if req.subject_id not in removed_refs
        ]
        new_refs = set(item.person_ids) | set(item.character_ids) | set(item.work_ids)
        existing_req_keys = {(r.right_type, r.subject_id) for r in item.requirements}
        for req in _default_requirements(item):
            if (req.right_type, req.subject_id) not in existing_req_keys:
                item.requirements.append(req)
                existing_req_keys.add((req.right_type, req.subject_id))

        current_refs = item.refs()
        rev = Revision(
            id=f"REV-ITEM-{item.id}-{len(self.case.revisions) + 1}",
            kind="revise_item",
            sequence=self._next_sequence(),
            item_id=item.id,
            old_value={"revision": snapshot["revision"], "refs": sorted(old_refs)},
            new_value={"revision": item.revision, "refs": sorted(current_refs)},
            source_record_id=source_record_id,
            note=note or "待发内容已修订；旧版本和旧审核事项保留",
        )
        self.case.revisions.append(rev)
        return rev

    update_release_item = revise_release_item
    revise_draft = revise_release_item

    # ------------------------------------------------------------------
    # Rights events
    # ------------------------------------------------------------------
    def add_objection(self, objection: Any = None, **kwargs: Any) -> Objection:
        if isinstance(objection, dict):
            obj = Objection.from_dict(objection, len(self.case.objections))
        elif isinstance(objection, Objection):
            obj = objection
        else:
            idx = len(self.case.objections) + 1
            obj = Objection(
                id=kwargs.pop("id", f"OBJ-{idx}"),
                sequence=kwargs.pop("sequence", self._next_sequence()),
                **kwargs,
            )
        if not obj.sequence:
            obj.sequence = self._next_sequence()
        existing = next((x for x in self.case.objections if x.id == obj.id), None)
        if existing:
            # Re-importing the same event is idempotent; a conflicting reuse of
            # a stable ID is refused rather than overwriting audit history.
            if (existing.target_item_ids == obj.target_item_ids
                    and existing.target_entity_ids == obj.target_entity_ids
                    and existing.reason == obj.reason):
                return existing
            raise ValueError(f"conflicting objection with same id already exists: {obj.id}")
        self.case.objections.append(obj)
        return obj

    def record_publication(self, publication: Any = None, **kwargs: Any) -> PublicationRecord:
        if isinstance(publication, dict):
            pub = PublicationRecord.from_dict(publication, len(self.case.publications))
        elif isinstance(publication, PublicationRecord):
            pub = publication
        else:
            idx = len(self.case.publications) + 1
            pub = PublicationRecord(
                id=kwargs.pop("id", f"PUB-{idx}"),
                item_id=kwargs.pop("item_id"),
                sequence=kwargs.pop("sequence", self._next_sequence()),
                **kwargs,
            )
        if not pub.sequence:
            pub.sequence = self._next_sequence()
        existing = next((x for x in self.case.publications if x.id == pub.id), None)
        if existing:
            same_fact = (
                existing.item_id == pub.item_id
                and existing.published_at == pub.published_at
                and existing.revision == pub.revision
                and existing.channel == pub.channel
            )
            if same_fact:
                return existing
            raise ValueError(
                f"conflicting publication record id {pub.id}; append a new event instead"
            )
        self.case.publications.append(pub)
        return pub

    publish = record_publication
    raise_objection = add_objection
    file_objection = add_objection

    def resolve_matter(self, matter_id: str, resolution_note: str, *, source: str | None = None) -> None:
        matter = next((m for m in self.case.matters if m.id == matter_id), None)
        if matter is None:
            raise KeyError(f"unknown matter: {matter_id}")
        matter.status = "resolved"
        matter.add_event("resolved", resolution_note, source)

    def verify_contact(self, contact_id: str, *, status: str = "verified", note: str = "") -> Contact:
        contact = next((c for c in self.case.contacts if c.id == contact_id), None)
        if contact is None:
            raise KeyError(contact_id)
        contact.authority_status = status
        if note:
            contact.notes = (contact.notes + "\n" + note).strip()
        return contact

    def verify_authorization(self, authorization_id: str, *, note: str = "") -> Authorization:
        auth = next((a for a in self.case.authorizations if a.id == authorization_id), None)
        if auth is None:
            raise KeyError(authorization_id)
        auth.verification_status = "verified"
        if note:
            auth.note = (auth.note + "\n" + note).strip()
        return auth

    def run_review(self, review_id: str = "REVIEW-CURRENT"):
        return self.review(review_id=review_id, archive=True)


def _read_json(path: str | Path) -> dict[str, Any]:
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))
