"""Command line entry point: ``python -m reviewdesk review data/case.json``."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .desk import ReviewDesk


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review-desk", description="合成文本权利与发布复核台")
    sub = parser.add_subparsers(dest="command", required=True)

    review = sub.add_parser(
        "review",
        aliases=["run", "check", "audit", "process"],
        help="读取案件 JSON 并输出复核报告",
    )
    review.add_argument("path", nargs="?", help="案件 JSON 路径")
    review.add_argument("--case", "--input", "--file", dest="option_path",
                        help="案件 JSON 路径（与位置参数等价）")
    review.add_argument("--json", dest="json_out", help="将机器可读报告写入该路径")
    review.add_argument("--markdown", "--md", dest="md_out",
                        help="将 Markdown 报告写入该路径")
    review.add_argument("--review-id", default="REVIEW-CURRENT")
    review.add_argument("--state-out", help="将含事项/审核历史的状态 JSON 写入该路径")
    review.add_argument("--no-intake-revisions", action="store_true",
                        help="不自动应用原始编辑通知中的改名/撤下")
    return parser


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        default_case = Path(__file__).resolve().parents[1] / "data" / "case.json"
        argv = ["review", str(default_case)]
    # Allow concise forms:
    #   python -m reviewdesk data/case.json
    #   python -m reviewdesk --case data/case.json
    review_flags = {
        "--case", "--input", "--file", "--json", "--markdown", "--md",
        "--state-out", "--review-id", "--no-intake-revisions",
    }
    if argv and argv[0] not in {"review", "run", "check", "audit", "process", "-h", "--help"}:
        if not argv[0].startswith("-") or argv[0] in review_flags or argv[0].split("=", 1)[0] in review_flags:
            argv = ["review", *argv]
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"review", "run", "check", "audit", "process"}:
        path = args.path or getattr(args, "option_path", None)
        if not path:
            parser.error("review requires a case JSON path")
        desk = ReviewDesk.from_file(
            path, intake_text_revisions=not args.no_intake_revisions
        )
        report = desk.run_review(args.review_id)
        payload = report.to_dict()
        if args.json_out:
            report.write_json(args.json_out)
        if args.md_out:
            report.write_markdown(args.md_out)
        if args.state_out:
            desk.save_json(args.state_out)
        if not args.json_out and not args.md_out:
            print(report.markdown())
        else:
            print(f"case={payload['case_id']} ready="
                  f"{payload['decision_summary']['ready_for_release']} hold="
                  f"{payload['decision_summary']['hold_for_confirmation']}")
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
