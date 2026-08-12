"""Phase 2 semantic correlation analysis for the ChatGPT transport probe.

Reads a sanitized probe JSONL and answers the Phase 2 questions:

  Q1  HTTP turn -> conversation/message/parent binding
  Q2  WS frames -> conversation / assistant turn binding
  Q3  reliable completion signal candidates (DOM-independent)
  Q4  ordinary vs @GitHub/tool completion parity
  +   cross-tab socket topology (independent vs multiplexed)

Outputs phase2-summary.json and a Markdown report. All inputs are sanitized
(hashes, enum fields, sizes only — no payload contents).
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

CASES = {"A", "B", "GITHUB", "concurrent", None}


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python phase2_summary.py <probe.jsonl> [output_dir]")
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

    # assign each row to the case active at its time (marker boundaries)
    ordered = sorted(rows, key=lambda r: r.get("ts", ""))
    case_of: dict[int, str | None] = {}
    current = None
    markers = []
    for i, row in enumerate(ordered):
        if row.get("kind") == "marker":
            current = row.get("case")
            markers.append((row.get("ts"), current, row.get("label")))
        case_of[i] = current

    summary: dict = {
        "records": len(rows),
        "markers": markers,
        "cases": {},
        "http_correlation": {},
        "ws_correlation": {},
        "completion_candidates": {},
        "completion_parity": {},
        "socket_topology": {},
        "unknowns": [],
    }

    # ---- per case buckets ----
    for idx, row in enumerate(ordered):
        case = case_of.get(idx)
        summary["cases"].setdefault(case, collections.Counter())
        summary["cases"][case][row.get("kind")] += 1

    # ---- Q1: HTTP turn -> conversation binding ----
    http = []
    for idx, row in enumerate(ordered):
        sem = row.get("semantic")
        if row.get("kind") != "request" or not sem:
            continue
        http.append(
            {
                "case": case_of.get(idx),
                "tab_id": row.get("tab_id"),
                "request_id": row.get("request_id"),
                "endpoint": sem.get("endpoint"),
                "action": sem.get("action"),
                "conversation_id_hash": sem.get("conversation_id_hash"),
                "parent_message_id_hash": sem.get("parent_message_id_hash"),
                "message_id_hashes": sem.get("message_id_hashes"),
                "doc_conversation_id_hash": row.get("doc_conversation_id_hash"),
                "body_size": sem.get("body_size"),
                "body_sha256": (sem.get("body_sha256") or "")[:12],
            }
        )
    summary["http_correlation"] = http

    # ---- Q2: WS socket -> conversation ----
    ws_rows = []
    for idx, row in enumerate(ordered):
        if row.get("kind") in ("ws_created", "ws_frame_sent", "ws_frame_received", "ws_closed"):
            ws_rows.append(
                {
                    "case": case_of.get(idx),
                    "tab_id": row.get("tab_id"),
                    "ws_request_id": row.get("ws_request_id"),
                    "kind": row.get("kind"),
                    "doc_conversation_id_hash": row.get("doc_conversation_id_hash"),
                    "conv_hash": (row.get("semantic") or {}).get("conversation_id_hash"),
                    "message_id_hash": (row.get("semantic") or {}).get("message_id_hash"),
                    "parent_id_hash": (row.get("semantic") or {}).get("parent_id_hash"),
                    "event_type": (row.get("semantic") or {}).get("event_type"),
                    "status": (row.get("semantic") or {}).get("status"),
                    "role": (row.get("semantic") or {}).get("role"),
                    "frame_size": row.get("frame_size"),
                    "nonce": row.get("semantic", {}).get("probe_nonce_label"),
                }
            )
    summary["ws_correlation"] = ws_rows

    # socket -> conversation mapping
    socket_conv: dict[str, set] = collections.defaultdict(set)
    for w in ws_rows:
        h = w.get("conv_hash") or w.get("doc_conversation_id_hash")
        if h:
            socket_conv[str(w.get("ws_request_id"))].add(str(h))
    summary["ws_socket_to_conversation"] = {k: sorted(v) for k, v in socket_conv.items()}

    # ---- Q3: completion signal candidates per case ----
    for case in ("A", "B", "GITHUB"):
        frames = [
            w
            for w in ws_rows
            if w.get("case") == case and w.get("kind") == "ws_frame_received"
        ]
        frames.sort(key=lambda w: w.get("frame_size") or 0)
        timeline = [
            {
                "event_type": f.get("event_type"),
                "status": f.get("status"),
                "role": f.get("role"),
                "size": f.get("frame_size"),
                "nonce": f.get("nonce"),
            }
            for f in frames
        ]
        # candidate: last frame event_type/status before nonce-containing frame
        nonce_frame = next((f for f in frames if f.get("nonce")), None)
        candidate = None
        if nonce_frame:
            idx_n = frames.index(nonce_frame)
            candidate = {
                "terminal_frame_before_nonce": timeline[idx_n - 1] if idx_n > 0 else None,
                "nonce_frame": timeline[idx_n],
                "frames_after_nonce": len(frames) - idx_n - 1,
            }
        summary["completion_candidates"][case] = {
            "frame_count": len(frames),
            "distinct_event_types": sorted({f["event_type"] for f in frames if f["event_type"]}),
            "distinct_statuses": sorted({f["status"] for f in frames if f["status"]}),
            "sequence": timeline[-20:],
            "candidate": candidate,
        }

    # ---- Q4: parity ----
    sig = {}
    for case in ("A", "B", "GITHUB"):
        cand = summary["completion_candidates"].get(case, {})
        sig[case] = {
            "distinct_event_types": cand.get("distinct_event_types"),
            "distinct_statuses": cand.get("distinct_statuses"),
        }
    summary["completion_parity"] = {
        "all_equal_event_types": sig["A"].get("distinct_event_types") == sig["B"].get("distinct_event_types") == sig["GITHUB"].get("distinct_event_types"),
        "all_equal_statuses": sig["A"].get("distinct_statuses") == sig["B"].get("distinct_statuses") == sig["GITHUB"].get("distinct_statuses"),
        "signatures": sig,
    }

    # ---- cross-tab topology ----
    concurrent_ws = [w for w in ws_rows if w.get("case") == "concurrent"]
    sockets = sorted({str(w.get("ws_request_id")) for w in concurrent_ws})
    tabs = sorted({str(w.get("tab_id")) for w in concurrent_ws})
    socket_tabs = collections.defaultdict(set)
    for w in concurrent_ws:
        socket_tabs[str(w.get("ws_request_id"))].add(str(w.get("tab_id")))
    summary["socket_topology"] = {
        "concurrent_sockets": sockets,
        "concurrent_tabs": tabs,
        "socket_to_tabs": {k: sorted(v) for k, v in socket_tabs.items()},
        "verdict": (
            "MULTIPLEXED (shared socket across tabs)"
            if len(sockets) == 1 and len(tabs) > 1
            else "PER-TAB SOCKETS" if len(sockets) > 1 else "INCONCLUSIVE"
        ),
    }

    # ---- unknowns ----
    for w in ws_rows:
        sem = w
        if w.get("conv_hash") is None and w.get("doc_conversation_id_hash") is None:
            summary["unknowns"].append({"ws_request_id": w.get("ws_request_id"), "reason": "no conversation binding found"})

    # ---- write outputs ----
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "phase2-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )

    lines = ["# Phase 2 Semantic Correlation Report", ""]
    lines.append(f"- records: {len(rows)}")
    lines.append(f"- markers: {len(markers)}")
    lines.append("- cases: " + ", ".join(f"{c}: {sum((summary['cases'].get(c) or collections.Counter()).values())}" for c in sorted(summary["cases"].keys(), key=lambda x: str(x))))
    lines.append("")
    lines.append("## HTTP turn -> conversation correlation")
    for h in http:
        lines.append(
            f"- {h.get('case')} req={h.get('request_id')} {h.get('endpoint')} "
            f"action={h.get('action')} conv={str(h.get('conversation_id_hash') or '')[:12]} "
            f"doc={str(h.get('doc_conversation_id_hash') or '')[:12]} "
            f"parent={str(h.get('parent_message_id_hash') or '')[:12]} "
            f"msg={[str(x or '')[:12] for x in h.get('message_id_hashes') or []]} size={h.get('body_size')}"
        )
    lines.append("")
    lines.append("## WS socket -> conversation")
    for s, convs in summary["ws_socket_to_conversation"].items():
        lines.append(f"- socket {s}: conversations {[c[:12] for c in convs]}")
    lines.append("")
    lines.append("## Completion candidates")
    for case in ("A", "B", "GITHUB"):
        c = summary["completion_candidates"].get(case, {})
        lines.append(f"- {case}: event_types={c.get('distinct_event_types')} statuses={c.get('distinct_statuses')} frames={c.get('frame_count')} candidate={json.dumps(c.get('candidate'), ensure_ascii=False)}")
    lines.append("")
    lines.append("## Parity")
    lines.append(f"- {json.dumps(summary['completion_parity'], ensure_ascii=False)}")
    lines.append("")
    lines.append("## Socket topology (concurrent)")
    lines.append(f"- {json.dumps(summary['socket_topology'], ensure_ascii=False)}")
    lines.append("")
    lines.append("## Unknowns")
    lines.append(f"- {len(summary['unknowns'])}")

    report = "\n".join(lines) + "\n"
    (out_dir / "phase2-report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
