"""Phase 3A shadow comparison report: transport-vs-DOM consistency.

Usage:
  python compare3a.py <sat3a-shadow-*.jsonl> [output_dir]
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def classify(turn_start: dict, complete: dict) -> str:
    """Auto-classify a turn without any UI markers."""
    if complete and complete.get("decision_transport"):
        return "capsule"
    if turn_start and turn_start.get("contains_at_github"):
        return "github"
    if turn_start and (turn_start.get("user_text_len") or 0) > 200:
        return "long"
    if complete and (complete.get("sse_bytes") or 0) > 3000:
        return "long"
    return "short"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python compare3a.py <probe.jsonl> [output_dir]")
        return 2
    path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else path.parent
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    # pair each turn_start with its turn_complete via (tab_id, request_id)
    starts = {f"{r.get('tab_id')}:{r.get('request_id')}": r for r in rows if r.get("kind") == "turn_start"}
    completed = [r for r in rows if r.get("kind") == "turn_complete"]
    for r in completed:
        r["_class"] = classify(
            starts.get(f"{r.get('tab_id')}:{r.get('request_id')}"),
            r,
        )

    report: dict = {
        "source": path.name,
        "records": len(rows),
        "cases": {},
        "turns": [],
        "summary": {},
    }

    report["turns"] = [
        {
            "class": r.get("_class"),
            "ts": r.get("ts"),
            "conversation_id_hash": (r.get("conversation_id_hash") or "")[:12],
            "message_id_hash": (r.get("message_id_hash") or "")[:12],
            "terminal_status": r.get("terminal_status"),
            "transport_completed": r.get("transport_completed"),
            "decision_transport": r.get("decision_transport"),
            "decision_dom": r.get("decision_dom"),
            "agreement": r.get("agreement"),
            "sse_bytes": r.get("sse_bytes"),
            "body_error": r.get("body_error"),
        }
        for r in completed
    ]

    per_case: dict[str, dict] = {}
    for r in completed:
        case = r.get("_class") or "unmarked"
        c = per_case.setdefault(
            case, {"turns": 0, "transport_completed": 0, "decision_both": 0, "match": 0, "mismatch": 0, "errors": 0}
        )
        c["turns"] += 1
        if r.get("transport_completed"):
            c["transport_completed"] += 1
        t = r.get("decision_transport")
        d = r.get("decision_dom")
        if t and d:
            c["decision_both"] += 1
            if t.get("decision") == d.get("decision") and t.get("delivery_token") == d.get("delivery_token"):
                c["match"] += 1
            else:
                c["mismatch"] += 1
        if r.get("body_error"):
            c["errors"] += 1
    report["cases"] = per_case

    total = len(completed)
    both = sum(c["decision_both"] for c in per_case.values())
    match = sum(c["match"] for c in per_case.values())
    report["summary"] = {
        "turns": total,
        "transport_completed": sum(c["transport_completed"] for c in per_case.values()),
        "decision_both": both,
        "match": match,
        "mismatch": sum(c["mismatch"] for c in per_case.values()),
        "consistency_pct": (100.0 * match / both) if both else None,
        "errors": sum(c["errors"] for c in per_case.values()),
        "pass_criteria": both > 0 and match == both,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "phase3a-summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )

    lines = ["# Phase 3A Shadow Consistency Report", ""]
    lines.append(f"- source: {path.name}")
    lines.append(f"- turns: {total}, transport_completed: {report['summary']['transport_completed']}")
    lines.append(f"- both detectors found decision: {both}, matches: {match}")
    lines.append(f"- consistency: {report['summary']['consistency_pct']}%")
    lines.append(f"- criterion (100% match): {report['summary']['pass_criteria']}")
    lines.append(f"- body errors: {report['summary']['errors']}")
    lines.append("")
    lines.append("## Per case")
    for case, c in sorted(per_case.items()):
        lines.append(
            f"- {case}: turns={c['turns']} completed={c['transport_completed']} "
            f"both={c['decision_both']} match={c['match']} mismatch={c['mismatch']} errors={c['errors']}"
        )
    lines.append("")
    lines.append("## Mismatches")
    for r in completed:
        if (r.get("decision_transport") and r.get("decision_dom")) and not (
            r["decision_transport"].get("decision") == r["decision_dom"].get("decision")
            and r["decision_transport"].get("delivery_token") == r["decision_dom"].get("delivery_token")
        ):
            lines.append(f"- {r.get('ts')} case={r.get('case')} T={json.dumps(r.get('decision_transport'), ensure_ascii=False)} D={json.dumps(r.get('decision_dom'), ensure_ascii=False)}")
    lines.append("")
    lines.append("## Turn detail")
    for r in completed:
        lines.append(
            f"- {r.get('ts')} case={r.get('case')} conv={(r.get('conversation_id_hash') or '')[:12]} "
            f"msg={(r.get('message_id_hash') or '')[:12]} term={r.get('terminal_status')} "
            f"completed={r.get('transport_completed')} T={bool(r.get('decision_transport'))} "
            f"D={bool(r.get('decision_dom'))} sse_bytes={r.get('sse_bytes')} err={r.get('body_error')}"
        )

    (out_dir / "phase3a-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
