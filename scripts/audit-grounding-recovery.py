"""Audit saved synthetic live runs against fresh HTTP history, without model calls."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
audits = []
with httpx.Client(base_url="http://127.0.0.1:18080/api/", timeout=15,
                  headers={"X-Tenant-ID": "course-demo", "X-Permission-Scopes": "hr_private"}) as client:
    for path in sorted(args.output.glob("history-*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        response = client.get("runs/history/"+saved["run_id"])
        response.raise_for_status()
        current = response.json()
        snapshot = current["assessment_grounding"]
        assert snapshot and snapshot["schema_version"] == "1.0"
        for field in ("assessment_grounding", "report", "citations"):
            assert current[field] == saved[field], (path.name, field)
        turns = {row["run_id"]: row for row in current["conversation_turns"]}
        assert turns[current["run_id"]]["assessment_grounding"] == snapshot
        for row in turns.values():
            previous_path = next((p for p in args.output.glob("history-*.json")
                if json.loads(p.read_text(encoding="utf-8"))["run_id"] == row["run_id"]), None)
            if previous_path:
                previous = json.loads(previous_path.read_text(encoding="utf-8"))
                assert row["assessment_grounding"] == previous["assessment_grounding"]

        events_path = args.output/("events-"+path.stem.removeprefix("history-")+".json")
        events = json.loads(events_path.read_text(encoding="utf-8"))
        event = next(row for row in events if row["type"] == "report.completed")
        assert event["data"]["assessment_grounding"] == snapshot
        anchors = {row["evidence_id"]: row for row in snapshot["anchors"]}
        assert len(anchors) == len(snapshot["anchors"])
        identities = {(r["candidate_id"], r["document_version_id"], r["chunk_id"], r["quote_start"], r["quote_end"])
                      for r in anchors.values()}
        assert len(identities) == len(anchors)
        for anchor in anchors.values():
            assert anchor["quote_end"]-anchor["quote_start"] == len(anchor["quote"])

        claims = {row["claim_id"]: row for row in snapshot["claims"]}
        reviews = {row["claim_id"]: row for row in snapshot["reviews"]}
        assert len(claims) == len(snapshot["claims"]) == len(reviews) == len(snapshot["reviews"])
        assert claims.keys() == reviews.keys()
        counts = Counter(row["verdict"] for row in reviews.values())
        assert snapshot["summary"]["supported"] == counts["supported"]
        assert snapshot["summary"]["errors"] == counts["error"]
        assert snapshot["summary"]["blocked"] == len(reviews)-counts["supported"]-counts["error"]
        for key, review in reviews.items():
            claim = claims[key]
            if review["verdict"] == "supported":
                assert claim["anchor_ids"] and review["anchor_ids"]
                assert set(review["anchor_ids"]) <= set(claim["anchor_ids"])
                assert {anchors[ref]["candidate_id"] for ref in claim["anchor_ids"]} == set(claim["candidate_ids"])
        dimensions = next(step["output"]["dimensions"] for step in current["steps"] if step["step_id"] == "build_dimensions")
        candidates = snapshot["candidates"]
        assert candidates == sorted(candidates, key=lambda row: (-row["confirmed_score"], -row["evidence_coverage_percent"], row["candidate_id"]))
        dimension_counts = Counter()
        for candidate in candidates:
            confirmed, possible, coverage = 0., 0., 0
            for row in candidate["dimensions"]:
                dimension_counts[row["evidence_status"]] += 1
                weight = dimensions[row["dimension_number"]-1]["weight_percent"]
                if row["score"] is None:
                    possible += weight
                    continue
                assert row["evidence_status"] in {"sufficient", "partial"}
                related = [review for key, review in reviews.items() if key.startswith(row["task_id"]+":")]
                assert related and all(item["verdict"] == "supported" for item in related)
                contribution = weight*row["score"]/5
                confirmed += contribution
                possible += contribution
                coverage += weight
            assert round(confirmed, 2) == candidate["confirmed_score"]
            assert round(possible, 2) == candidate["possible_score"]
            assert coverage == candidate["evidence_coverage_percent"]

        groups = snapshot["verification"]["candidates"].values()
        for group in groups:
            assert len(group["items"]) <= 8
            assert len({row["item_id"] for row in group["items"]}) == len(group["items"])
        if counts["error"]:
            assert snapshot["status"] == "degraded"
            assert any(group["technical_actions"] for group in groups)
        sections = []
        active = False
        for line in current["report"].splitlines():
            if line.startswith("##"):
                active = line in {"### 主要依据", "### 材料中的风险依据"}
            elif active:
                sections.append(line)
        published = "\n".join(sections)
        for key, claim in claims.items():
            if ":highlights:" in key or ":risk_points:" in key:
                if reviews[key]["verdict"] == "supported":
                    assert claim["text"] in published
                else:
                    assert claim["text"] not in published
        citations = []
        for citation in current["citations"]:
            response = client.get(citation["url"].removeprefix("/api/"))
            response.raise_for_status()
            resolved = response.json()
            assert resolved["candidate_id"] == citation["candidate"]
            assert resolved["content"][resolved["quote_start"]:resolved["quote_end"]] == citation["quote"]
            citations.append(citation["ref"])
        audits.append(dict(run_id=current["run_id"], turn_no=current["turn_no"],
            status=current["status"], grounding_status=snapshot["status"], summary=snapshot["summary"],
            dimension_statuses=dict(dimension_counts), citations_checked=len(citations),
            anchors=len(anchors), candidates=[{k:c[k] for k in ("candidate_id", "confirmed_score", "possible_score", "evidence_coverage_percent")} for c in candidates],
            sse_history_equal=True, refresh_equal=True, numeric_aggregation_equal=True,
            claim_publication_filter_equal=True))
assert audits, "No saved histories to audit"
(args.output/"grounding-audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(audits, ensure_ascii=False))
