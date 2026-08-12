"""Summarize a sanitized ChatGPT transport probe JSONL file.

Usage:
  python summary.py <probe.jsonl> [output.md]

Outputs a case-by-case summary: request distribution, WebSocket frame stats,
payload sizes/hashes, and termination info. All input is already sanitized
(no credentials or payload contents).
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python summary.py <probe.jsonl> [output.md]")
        return 2
    path = Path(sys.argv[1])
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    out: list[str] = []
    out.append("# ChatGPT Transport Probe Summary")
    out.append("")
    out.append(f"- total records: {len(rows)}")
    out.append(f"- source: `{path.name}` (sanitized; no credentials or payload contents)")

    cases = collections.Counter(row.get("case") for row in rows if row.get("kind") != "marker")
    out.append("- cases: " + ", ".join(f"case{c}: {n}" for c, n in sorted(cases.items())))

    kinds = collections.Counter(row.get("kind") for row in rows)
    out.append("")
    out.append("## Record kinds")
    for kind, n in kinds.most_common():
        out.append(f"- {kind}: {n}")

    requests = [row for row in rows if row.get("kind") == "request"]
    if requests:
        out.append("")
        out.append("## Requests")
        methods = collections.Counter(row.get("method") for row in requests)
        out.append("- methods: " + ", ".join(f"{m}: {n}" for m, n in methods.most_common()))
        types = collections.Counter(row.get("resource_type") for row in requests)
        out.append("- resource types: " + ", ".join(f"{t}: {n}" for t, n in types.most_common()))
        paths = collections.Counter(row.get("url_path") for row in requests)
        out.append("")
        out.append("### URL paths (top 30)")
        for p, n in paths.most_common(30):
            out.append(f"- {n:4d}  {p}")
        out.append("")
        out.append("### Payloads (POST, top 20 by size)")
        post = [row for row in requests if row.get("method") == "POST"]
        for row in sorted(post, key=lambda r: -(r.get("payload_size") or 0))[:20]:
            out.append(
                f"- {row['url_path']}  size={row.get('payload_size')}  "
                f"sha256={str(row.get('payload_sha256') or '')[:12]}  "
                f"schema={json.dumps(row.get('payload_schema') or {}, ensure_ascii=False)[:200]}"
            )
        byts = sum(row.get("payload_size") or 0 for row in post)
        out.append(f"- total POST payload bytes: {byts}")

    ws = [row for row in rows if row.get("kind", "").startswith("ws_")]
    if ws:
        out.append("")
        out.append("## WebSocket")
        ws_kinds = collections.Counter(row.get("kind") for row in ws)
        for k, n in ws_kinds.most_common():
            out.append(f"- {k}: {n}")
        frames = [row for row in rows if row.get("kind") in ("ws_frame_sent", "ws_frame_received")]
        sent = [row for row in frames if row["kind"] == "ws_frame_sent"]
        recv = [row for row in frames if row["kind"] == "ws_frame_received"]
        out.append(f"- frames sent: {len(sent)}, received: {len(recv)}")
        if recv:
            sizes = sorted((row.get("payload_size") or 0) for row in recv)
            out.append(
                f"- received frame payload bytes: total={sum(sizes)} "
                f"min={sizes[0]} max={sizes[-1]}"
            )
        closed = [row for row in rows if row.get("kind") == "ws_closed"]
        if closed:
            out.append(f"- ws closed: {len(closed)} (codes: {sorted(set(row.get('code') for row in closed))})")

    failed = [row for row in rows if row.get("kind") == "failed"]
    if failed:
        out.append("")
        out.append("## Failed/terminated")
        for row in failed[:20]:
            out.append(f"- {row.get('error_text')} canceled={row.get('canceled')}")

    summary = "\n".join(out) + "\n"
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(summary, encoding="utf-8")
        print(f"summary written to {sys.argv[2]}")
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
