import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reviewdesk import ReviewDesk, assess_infringement, review_dict
from reviewdesk.model import Scope


def base_payload():
    return {
        "case_id": "synthetic-rights-case",
        "entities": [
            {"id": "P-1", "name": "林岚", "type": "表演者", "labels": ["performer"]},
            {"id": "C-1", "name": "秋蘅", "type": "角色"},
            {"id": "W-1", "name": "雾桥", "type": "作品"},
        ],
    }


def full_scope():
    return {
        "purposes": ["release", "commercial"],
        "channels": ["platform_review", "streaming"],
        "uses": ["short_film", "likeness", "performance", "character_use", "work_release"],
        "commercial": True,
    }


class ReviewDeskBehaviorTest(unittest.TestCase):
    def test_generic_consent_note_is_not_release_authorization(self):
        payload = base_payload()
        payload["release_items"] = [{
            "id": "I-1",
            "person_ids": ["P-1"],
            "character_ids": ["C-1"],
            "work_ids": ["W-1"],
            "scope": full_scope(),
            "notes": ["备注：已获同意"],
        }]
        report = ReviewDesk.from_dict(payload).review().to_dict()
        item = report["items"][0]
        self.assertFalse(item["ready"])
        self.assertEqual(item["decision"], "hold_for_confirmation")
        self.assertIn("generic_consent_note_is_not_authorization", item["reasons"])
        self.assertTrue(any(
            f["code"] == "GENERIC_CONSENT_NOT_SUFFICIENT"
            for f in item["note_findings"]
        ))
        # A stable matter is opened and remains pending.
        self.assertTrue(any(m["id"].endswith("NOTE-I-1-1") for m in report["matters"]))

    def test_attached_generic_note_overrides_otherwise_cleared_item(self):
        payload = base_payload()
        payload["records"] = [{"id": "N-1", "text": "备注：已获同意"}]
        payload["authorizations"] = [{
            "id": "A-1", "person_ids": ["P-1"], "right_types": ["*"],
            "scope": {"purposes": ["release"]}, "verification_status": "verified",
        }]
        payload["release_items"] = [{
            "id": "I-1", "person_ids": ["P-1"],
            "scope": {"purposes": ["release"]}, "note_record_ids": ["N-1"],
        }]
        item = review_dict(payload)["items"][0]
        self.assertEqual(item["decision"], "hold_for_confirmation")
        self.assertFalse(item["can_release"])
        self.assertFalse(item["can_publish"])

    def test_person_character_work_and_scope_cannot_replace_each_other(self):
        payload = base_payload()
        payload["authorizations"] = [{
            "id": "A-character-only",
            "character_ids": ["C-1"],
            "right_types": ["character_use"],
            "scope": full_scope(),
            "verification_status": "verified",
        }]
        payload["release_items"] = [{
            "id": "I-1",
            "person_ids": ["P-1"],
            "character_ids": ["C-1"],
            "work_ids": ["W-1"],
            "scope": full_scope(),
        }]
        report = review_dict(payload)
        states = {
            (r["right_type"], r["subject_id"]): r["state"]
            for item in report["items"]
            for r in item["requirement_reviews"]
        }
        self.assertEqual(states[("character_use", "C-1")], "grounded")
        self.assertEqual(states[("likeness", "P-1")], "missing_authorization")
        self.assertEqual(states[("performance", "P-1")], "missing_authorization")
        self.assertEqual(states[("work_release", "W-1")], "missing_authorization")
        self.assertFalse(report["items"][0]["ready"])

    def test_training_license_does_not_cover_public_release(self):
        payload = base_payload()
        payload["authorizations"] = [{
            "id": "A-train",
            "person_ids": ["P-1"],
            "right_types": ["likeness", "performance", "*"],
            "scope": {
                "purposes": ["training"],
                "uses": ["training_material"],
            },
            "verification_status": "verified",
        }]
        payload["release_items"] = [{
            "id": "I-1",
            "person_ids": ["P-1"],
            "scope": {"purposes": ["release"], "channels": ["streaming"]},
        }]
        report = review_dict(payload)
        req = report["items"][0]["requirement_reviews"][0]
        self.assertEqual(req["state"], "scope_not_covered")
        self.assertFalse(req["is_satisfied"])
        self.assertIn("release", req["matching_bases"][0]["scope_match"]["missing"]["purposes"])

    def test_same_root_evidence_and_duplicate_grant_count_once(self):
        payload = base_payload()
        payload["records"] = [
            {"id": "M-1", "text": "权利人邮件授权", "kind": "permission-note",
             "root_record_id": "E-1"},
            {"id": "M-2", "text": "群聊转发：听说同意", "kind": "forwarded-claim",
             "root_record_id": "E-1"},
            {"id": "M-3", "text": "再次转发同一邮件", "kind": "forwarded-claim",
             "root_record_id": "E-1"},
        ]
        payload["authorizations"] = [
            {"id": "A-1", "root_authorization_id": "G-1", "person_ids": ["P-1"],
             "right_types": ["all"], "scope": full_scope(), "verification_status": "verified",
             "evidence_ids": ["E-1"]},
            {"id": "A-2", "root_authorization_id": "G-1", "person_ids": ["P-1"],
             "right_types": ["all"], "scope": full_scope(), "verification_status": "verified",
             "evidence_ids": ["E-1"]},
        ]
        report = review_dict(payload)
        self.assertEqual(len(report["authorization_bases"]), 1)
        self.assertEqual(report["authorization_bases"][0]["independent_authorization_count"], 1)
        group = next(g for g in report["evidence_groups"] if g["root_evidence_id"] == "E-1")
        self.assertTrue(group["is_repeated_relay"])
        self.assertEqual(set(group["record_ids"]), {"M-1", "M-2", "M-3"})

    def test_objection_does_not_erase_publication_and_blocks_future_release(self):
        desk = ReviewDesk.from_dict({
            **base_payload(),
            "authorizations": [{
                "id": "A-1", "person_ids": ["P-1"], "character_ids": ["C-1"],
                "work_ids": ["W-1"], "right_types": ["*"], "scope": full_scope(),
                "verification_status": "verified",
            }],
            "release_items": [{
                "id": "I-1", "person_ids": ["P-1"], "character_ids": ["C-1"],
                "work_ids": ["W-1"], "scope": full_scope(),
            }],
        })
        before = desk.run_review("R-before")
        self.assertTrue(before.to_dict()["items"][0]["ready"])
        desk.record_publication(id="PUB-1", item_id="I-1", published_at="2026-11-01",
                                channel="streaming")
        desk.add_objection(id="OBJ-1", target_item_ids=["I-1"],
                           reason="近亲属对授权范围提出异议", received_at="2026-11-20")
        after = desk.run_review("R-after").to_dict()
        self.assertEqual(len(after["publication_history"]), 1)
        self.assertEqual(after["publication_history"][0]["id"], "PUB-1")
        self.assertTrue(after["publication_history"][0]["record_retained_after_objection"])
        item = after["items"][0]
        self.assertFalse(item["ready"])
        self.assertIn("open_rights_objection", item["reasons"])
        self.assertTrue(any(m["id"] == "MAT-OBJ-1" for m in after["matters"]))

    def test_rename_shows_impact_and_preserves_existing_matter_title(self):
        desk = ReviewDesk.from_dict({
            **base_payload(),
            "release_items": [{
                "id": "I-1", "character_ids": ["C-1"], "scope": full_scope(),
            }],
        }, intake_text_revisions=False)
        first = desk.run_review("R1").to_dict()
        matter_id = first["items"][0]["requirement_reviews"][0]["requirement_id"]
        matter_id = f"MAT-{matter_id}"
        old_title = next(m["title"] for m in first["matters"] if m["id"] == matter_id)
        self.assertIn("秋蘅", old_title)

        desk.rename_entity("C-1", "秋衡", source_record_id="M-31")
        second = desk.run_review("R2").to_dict()

        self.assertEqual(desk.case.entity("C-1").name, "秋衡")
        self.assertIn("秋蘅", desk.case.entity("C-1").aliases)
        impact = next(i for i in second["impacts"] if i["revision"]["kind"] == "rename")
        self.assertIn("I-1", impact["affected_item_ids"])
        self.assertIn(matter_id, impact["affected_matter_ids"])
        self.assertIn("R1", impact["prior_review_ids"])
        matter = next(m for m in second["matters"] if m["id"] == matter_id)
        self.assertIn("秋蘅", matter["title"])  # original judgment snapshot not rewritten
        self.assertNotEqual(matter["subject_snapshot"]["subject_name_at_first_review"], "秋衡")

    def test_work_removal_and_later_item_revision_keep_matters(self):
        desk = ReviewDesk.from_dict({
            **base_payload(),
            "release_items": [{
                "id": "I-1", "work_ids": ["W-1"], "scope": full_scope(),
            }],
        }, intake_text_revisions=False)
        first = desk.run_review("R1").to_dict()
        matter_id = f"MAT-{first['items'][0]['requirement_reviews'][0]['requirement_id']}"

        desk.remove_work("W-1", source_record_id="EDIT-REMOVE-1")
        removed = desk.run_review("R2").to_dict()
        req = removed["items"][0]["requirement_reviews"][0]
        self.assertEqual(req["state"], "subject_removed")
        impact = next(i for i in removed["impacts"] if i["revision"]["kind"] == "remove")
        self.assertIn(matter_id, impact["affected_matter_ids"])

        desk.revise_release_item("I-1", new_revision="draft-2", work_ids=[])
        final = desk.run_review("R3").to_dict()
        self.assertEqual(final["items"][0]["requirement_reviews"], [])
        matter = next(m for m in final["matters"] if m["id"] == matter_id)
        self.assertEqual(matter["status"], "open")
        self.assertTrue(matter["manual_closure_required"])
        removal_impact = next(i for i in final["impacts"] if i["revision"]["kind"] == "remove")
        self.assertIn("I-1", removal_impact["affected_item_ids"])
        self.assertTrue(removal_impact["affected_item_versions"])

    def test_objection_request_to_remove_does_not_silently_remove_work(self):
        payload = base_payload()
        payload["records"] = [{
            "id": "OBJ-TEXT",
            "kind": "objection-note",
            "text": "权利联系人提出异议，请撤下作品《雾桥》。",
        }]
        desk = ReviewDesk.from_dict(payload)
        self.assertEqual(desk.case.entity("W-1").status, "active")
        self.assertFalse(any(r.kind == "remove" for r in desk.case.revisions))
        report = desk.review().to_dict()
        self.assertTrue(any(m["id"] == "MAT-CLAIM-OBJ-TEXT-1" for m in report["matters"]))

    def test_multiple_relatives_are_separate_and_not_auto_ranked(self):
        payload = base_payload()
        payload["entities"][0]["labels"].append("deceased")
        payload["contacts"] = [
            {"id": "C-A", "represents_id": "P-1", "relationship": "配偶",
             "authority_status": "unverified"},
            {"id": "C-B", "represents_id": "P-1", "relationship": "子女",
             "authority_status": "unverified"},
        ]
        report = review_dict(payload)
        contacts = report["contacts"]
        self.assertEqual(len(contacts), 2)
        self.assertTrue(all(c["multiple_relatives"] for c in contacts))
        self.assertFalse(any(c["authority_conflict"] for c in contacts))

        desk = ReviewDesk.from_dict(payload)
        desk.verify_contact("C-A", status="verified")
        desk.verify_contact("C-B", status="rejected")
        report = desk.review().to_dict()
        self.assertTrue(any(c["authority_conflict"] for c in report["contacts"]))

    def test_similarity_and_heat_do_not_become_infringement_or_clearance(self):
        result = assess_infringement(similarity=0.99, heat=10_000_000)
        self.assertEqual(result.conclusion, "not_determined")
        self.assertFalse(result.automatic_infringement_conclusion)
        payload = base_payload()
        payload["similarity_score"] = 0.98
        payload["public_sentiment"] = "negative"
        payload["release_items"] = [{"id": "I-1", "work_ids": ["W-1"]}]
        report = review_dict(payload)
        self.assertFalse(report["policy"]["automatic_infringement_from_similarity_or_heat"])
        self.assertEqual(report["infringement"]["conclusion"], "not_determined")
        self.assertIn("similarity_score", report["excluded_signals"])
        self.assertFalse(report["items"][0]["ready"])

    def test_sample_case_file_is_reviewable_and_deterministic(self):
        path = ROOT / "data" / "case.json"
        desk1 = ReviewDesk.from_file(path)
        report1 = desk1.run_review("R").to_json()
        desk2 = ReviewDesk.from_file(path)
        report2 = desk2.run_review("R").to_json()
        self.assertEqual(json.loads(report1), json.loads(report2))
        report = json.loads(report1)
        ids = {e["id"]: e for e in report["entities"]}
        self.assertEqual(ids["C-8"]["name"], "秋衡")
        portrayal = next(r for r in report["relations"] if r["kind"] == "portrayal")
        self.assertEqual((portrayal["from_id"], portrayal["to_id"]), ("P-4", "C-8"))
        # The forwarded "林老师同意了" is a candidate, not a confirmed P-4 link.
        m23 = next(c for c in report["claims"] if "M-23" in c["source_record_ids"])
        self.assertTrue(m23["hearsay"])
        self.assertNotIn("P-4", m23["refs"]["mentioned_entity_ids"])
        self.assertIn("P-4", m23["refs"]["candidate_entity_ids"])

    def test_cli_review_sample_case(self):
        proc = subprocess.run(
            [sys.executable, "-m", "reviewdesk", "review", "data/case.json"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("发布复核报告", proc.stdout)
        self.assertIn("不会被静默关闭", proc.stdout)


class ScopeTest(unittest.TestCase):
    def test_training_only_scope_rejects_release_and_explicit_commercial_exclusion(self):
        granted = Scope(purposes={"training"})
        requested = Scope(purposes={"release"})
        self.assertFalse(granted.allows(requested).ok)

        lecture = Scope(
            purposes={"education"}, channels={"school_closed_lecture"}, uses={"clip"},
            denied_uses={"commercial", "commercial_short", "short_film"},
            denied_purposes={"commercial"}, commercial=False,
        )
        commercial_short = Scope(purposes={"release", "commercial"}, commercial=True,
                                 uses={"commercial_short"})
        match = lecture.allows(commercial_short)
        self.assertFalse(match.ok)
        self.assertTrue(match.denied_hits)


if __name__ == "__main__":
    unittest.main()
