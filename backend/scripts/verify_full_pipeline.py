"""Exercise the deployed HTTP API with clearly synthetic PDF resumes.

Run on the host with httpx and reportlab installed. All evidence is saved under
--output; rerunning a stage reuses its test employees, library and documents.
No API credentials are read or written by this verifier.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


class Verification:
    def __init__(self, base: str, output: Path, tag: str):
        self.output = output
        output.mkdir(parents=True, exist_ok=True)
        self.state_path = output / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {"tag": tag, "department": f"E2E验收组{tag}"}
        self.client = httpx.Client(base_url=base.rstrip("/") + "/api/", timeout=300, headers={"X-Tenant-ID": "course-demo", "X-Permission-Scopes": "hr_private"})

    def save(self, name, data):
        (self.output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def persist(self):
        self.save("state", self.state)

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            raise AssertionError(f"{method} {path}: HTTP {response.status_code}: {response.text[:1500]}")
        return response.json()

    def check(self, name, detail):
        print(json.dumps({"check": name, "detail": detail}, ensure_ascii=False), flush=True)
        self.state.setdefault("checks", {})[name] = detail
        self.persist()

    def ingest(self):
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.enums import TA_CENTER
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

        tag = self.state["tag"]
        employees = self.request("GET", "employees")
        libraries = self.request("GET", "knowledge-bases")
        library_name = f"完整链路验收（虚构数据）{tag}"
        library = next((x for x in libraries if x["name"] == library_name), None)
        if library is None:
            library = self.request("POST", "knowledge-bases", json={"name": library_name, "description": "自动验收使用的虚构简历，覆盖 PDF、MinerU、切分、索引、检索及评估报告。", "permission_scope": "hr_private"})
        self.state["knowledge_base"] = library
        self.persist()
        profiles = [
            ("A", "Alice Test", 7, [
                ("Professional summary", "Seven years of Python backend engineering. Led enterprise knowledge-base RAG delivery, retrieval evaluation and production operations."),
                ("Project: Atlas Enterprise RAG (2024-2026)", "Technical lead for a synthetic enterprise knowledge assistant. Built Python, FastAPI, LangGraph, Milvus hybrid retrieval, embedding and reranking. Used parent-child chunks and page-level citations. Personally owned the retrieval and evaluation pipeline."),
                ("Measured results", "Evaluated 300 manually labelled questions. Recall@10 improved from 70% to 88%; answer groundedness improved from 76% to 91%. Reduced median response latency from 4.5 to 2.8 seconds. These are fictional test metrics."),
                ("Engineering and collaboration", "Deployed Docker services with PostgreSQL and Redis, added health checks, retries and rollback. Mentored three engineers and collaborated with HR to define acceptance criteria."),
                ("Limits and verification", "No evidence of training foundation models or managing GPU clusters. Validate the retrieval benchmark and individual contribution during interview."),
            ]),
            ("B", "Bob Test", 5, [
                ("Professional summary", "Five years of Python backend engineering. Strong FastAPI, SQL, Docker, PostgreSQL and Redis experience. Participated in an internal RAG prototype but did not lead production RAG delivery."),
                ("Project: Orion Workflow Platform (2022-2026)", "Backend developer responsible for REST APIs, PostgreSQL query optimisation, Redis caching and Docker deployment. Improved API throughput from 150 to 300 requests per second in a synthetic load test."),
                ("Project: Internal RAG Prototype (2025)", "Integrated a vector search API for a small knowledge-base proof of concept. Assisted with document ingestion. No ownership of hybrid retrieval, reranking or production evaluation. No measured RAG recall or groundedness results."),
                ("Engineering and collaboration", "Wrote API integration tests, reviewed changes with four colleagues, and documented deployment steps. Reliable delivery in established backend services."),
                ("Limits and verification", "Production RAG operations and retrieval evaluation are unverified. Needs mentoring on LangGraph orchestration and parent-child chunking. These records are fictional test data."),
            ]),
        ]
        self.state.setdefault("employees", {})
        self.state.setdefault("documents", {})
        existing_docs = self.request("GET", "documents", params={"knowledge_base_id": library["id"]})
        for letter, name, years, sections in profiles:
            employee_no = f"E2E{tag}{letter}"
            employee = next((x for x in employees if x["employee_no"] == employee_no), None)
            if employee is None:
                employee = self.request("POST", "employees", json={"employee_no": employee_no, "name": f"链路验收{letter}（虚构）", "region": "上海", "current_position": "AI应用工程师", "job_level": "L5", "years_of_experience": years, "department": self.state["department"]})
            self.state["employees"][letter] = employee
            self.persist()
            pdf = self.output / f"synthetic-resume-{letter}.pdf"
            if not pdf.exists():
                styles = getSampleStyleSheet()
                styles["Title"].fontSize = 20
                styles["Title"].alignment = TA_CENTER
                styles["Heading2"].textColor = colors.HexColor("#173b64")
                story = [Paragraph("SYNTHETIC TEST DATA", styles["Title"]), Paragraph(f"{name} | Candidate {employee_no}", styles["Heading2"]), Paragraph(f"Shanghai | {years} years experience | AI Application Engineer", styles["Normal"]), Spacer(1, 14)]
                for heading, body in sections:
                    story += [Paragraph(heading, styles["Heading2"]), Paragraph(body, styles["Normal"]), Spacer(1, 8)]
                SimpleDocTemplate(str(pdf), topMargin=38, bottomMargin=38).build(story)
            doc = self.state["documents"].get(letter) or next((x for x in existing_docs if x["candidate_id"] == employee_no), None)
            if doc is None:
                with pdf.open("rb") as stream:
                    doc = self.request("POST", "documents", files={"file": (pdf.name, stream, "application/pdf")}, data={"employee_id": employee["id"], "knowledge_base_id": library["id"], "title": f"链路验收{letter}简历（虚构 PDF）", "document_type": "resume", "permission_scope": "hr_private"})
            self.state["documents"][letter] = doc
            self.persist()
            self.check(f"upload_{letter}", {"document_id": doc["id"], "employee_no": employee_no})
        self.wait_documents()

    def wait_documents(self):
        remaining = dict(self.state["documents"])
        deadline = time.monotonic() + 1800
        previous = {}
        while remaining and time.monotonic() < deadline:
            for letter, document in list(remaining.items()):
                doc = self.request("GET", f"documents/{document['id']}")
                status = {"pipeline": doc["pipeline_status"], "parse": doc["parse_status"], "chunk": doc["chunk_status"], "index": doc["index_status"], "progress": doc["jobs"][0]["progress"] if doc["jobs"] else None}
                if previous.get(letter) != status:
                    print(json.dumps({"document": letter, **status}, ensure_ascii=False), flush=True)
                    previous[letter] = status
                self.save(f"document-{letter}", doc)
                if doc["pipeline_status"] == "failed":
                    raise AssertionError(f"Document {letter} failed: {doc['jobs']} {doc['index_jobs']}")
                if doc["pipeline_status"] != "ready":
                    continue
                parser = doc["jobs"][0]
                assert "mineru" in parser["parser_name"].lower(), parser
                assert "3.4.4" in (str(parser["parser_version"]) if parser["parser_version"] else parser["parser_name"]), parser
                chunks = self.request("GET", f"documents/{document['id']}/chunks")
                self.save(f"chunks-{letter}", chunks)
                parents = {c["id"]: c for c in chunks["chunks"] if c["level"] == "parent"}
                children = [c for c in chunks["chunks"] if c["level"] == "child"]
                assert parents and children, "Missing parent/child chunks"
                assert all(c["parent_chunk_id"] in parents for c in children), "Broken parent reference"
                assert all(c["source_locators"] and c["page_start"] is not None for c in children), "Missing PDF locators"
                with httpx.Client(timeout=30) as preview_client:
                    preview = preview_client.get(doc["file"]["preview_url"])
                    preview.raise_for_status()
                    assert preview.content.startswith(b"%PDF"), "Invalid original PDF download"
                self.check(f"pipeline_{letter}", {"document_id": document["id"], "parser": parser["parser_name"], "version": parser["parser_version"] or parser["parser_name"], "parents": len(parents), "children": len(children), "indexed": doc["index_jobs"][0]["indexed_count"], "original_pdf": "accessible"})
                remaining.pop(letter)
            if remaining:
                time.sleep(5)
        assert not remaining, f"Document pipeline timed out: {list(remaining)}"

    def search(self):
        candidates = [x["employee_no"] for x in self.state["employees"].values()]
        for mode in ("search", "hybrid-search"):
            hits = self.request("POST", f"evidence/{mode}", json={"query": "enterprise RAG Python hybrid retrieval evaluation recall", "candidate_ids": candidates, "limit": 8})
            self.save(mode, hits)
            assert hits and all(x["candidate_id"] in candidates for x in hits), hits
            self.check(mode, {"hits": len(hits), "candidates": sorted({x["candidate_id"] for x in hits})})
        query = f"请在部门为{self.state['department']}的人才中筛选有企业知识库 RAG、Python 开发与检索评测经验的工程师。仅该部门，不限制其他人员属性。"
        result = self.request("POST", "talent-search", json={"query": query, "include_evidence_pack": True, "limit": 8})
        self.save("talent-search", result)
        assert set(result["candidate_ids"]) == set(candidates), result
        assert result["chunks"] and result["evidence_packs"], "Missing retrieved evidence"
        self.check("talent-search", {"candidates": result["candidate_ids"], "chunks": len(result["chunks"]), "packs": len(result["evidence_packs"]), "status": result.get("evidence_status")})
        self.verify_citation(hits[0]["chunk_id"])

    def verify_citation(self, chunk_id):
        citation = self.request("GET", f"evidence/citations/{chunk_id}")
        assert citation["content"] and citation["source_locators"] and citation["page_start"] is not None, citation
        self.save("citation", citation)
        for headers in ({"X-Tenant-ID": "other-test-tenant"}, {"X-Permission-Scopes": "public"}):
            denied = self.client.get(f"evidence/citations/{chunk_id}", headers=headers)
            assert denied.status_code == 404, denied.text
        bad_quote = self.client.get(f"evidence/citations/{chunk_id}", params={"quote_start": 0, "quote_end": len(citation["content"]) + 1})
        assert bad_quote.status_code == 422, bad_quote.text
        self.check("citation", {"chunk_id": chunk_id, "page": citation["page_start"], "locators": len(citation["source_locators"]), "tenant_and_scope_denied": True, "bad_quote_denied": True})

    def consume(self, run, label, after=0):
        events = []
        deadline = time.monotonic() + 1200
        with self.client.stream("GET", f"runs/{run['run_id']}/events") as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Run {run['run_id']} timed out")
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event["sequence"] <= after:
                    continue
                events.append(event)
                self.save(f"events-{label}", events)
                if event["type"] != "step.progress":
                    print(json.dumps({"run": label, "sequence": event["sequence"], "type": event["type"], "data": event["data"] if event["type"] in {"run.failed", "hitl.required"} else {k: v for k, v in event["data"].items() if k in {"step_id", "summary", "status", "completed", "total"}}}, ensure_ascii=False), flush=True)
                if event["type"] in {"hitl.required", "run.completed", "run.failed", "run.cancelled"}:
                    break
        assert events, "No SSE events"
        return events

    def run(self):
        question = f"请只评估部门为{self.state['department']}的候选人，岗位为 AI 应用工程师。要求 Python 后端开发、企业知识库 RAG 项目经验、检索评测经验和 Docker 部署经验。请比较个人贡献、量化成果和证据不足项，给出带原文引用的推荐报告。这些为虚构测试数据。"
        run = self.request("POST", "runs", json={"message": question})
        self.state["run"] = run
        self.persist()
        events = self.consume(run, "evaluation")
        self.assert_completed(run, events, "evaluation")

    def assert_completed(self, run, events, label):
        assert events[-1]["type"] == "run.completed", events[-1]
        assert [x["sequence"] for x in events] == list(range(events[0]["sequence"], events[-1]["sequence"] + 1)), "Non-contiguous SSE sequence"
        required = {"step.started", "step.completed", "report.completed", "run.completed"}
        assert required <= {x["type"] for x in events}
        history = self.request("GET", f"runs/history/{run['run_id']}")
        self.save(f"history-{label}", history)
        assert history["status"] == "completed" and history["report"] and history["citations"], history
        steps = {step["step_id"]: step for step in history["steps"]}
        parallel = steps["parallel_assessment"]["output"]
        assert parallel["completed"] == parallel["total"] > 0, parallel
        assert parallel["extraction_failures"] == 0, parallel
        scoring = steps["score_candidates"]["output"]
        expected = {employee["employee_no"] for employee in self.state["employees"].values()}
        assert set(scoring["ranking"]) == expected, scoring["ranking"]
        (self.output / f"report-{label}.md").write_text(history["report"], encoding="utf-8")
        cases = self.request("GET", "evaluation/cases", params={"source_run_id": run["run_id"], "limit": 100})
        self.save(f"cases-{label}", cases)
        assert cases["items"], "Missing live module evaluation cases"
        self.check(label, {"run_id": run["run_id"], "conversation_id": run["conversation_id"], "turn_no": run["turn_no"], "events": len(events), "report_chars": len(history["report"]), "citations": len(history["citations"]), "cases": len(cases["items"]), "case_statuses": sorted({x["status"] for x in cases["items"]})})
        resolved = []
        for citation in history["citations"]:
            url = urlsplit(citation["url"])
            assert url.path.startswith("/api/evidence/citations/") and not url.netloc, citation
            response = self.client.get(url.path.removeprefix("/api/") + "?" + url.query)
            response.raise_for_status()
            row = response.json()
            assert row["candidate_id"] == citation["candidate"], citation
            assert row["content"][row["quote_start"]:row["quote_end"]] == citation["quote"], citation
            assert row["source_locators"] and row["page_start"] is not None, row
            resolved.append({"ref": citation["ref"], "chunk_id": row["chunk_id"], "page": row["page_start"], "quote_verified": True})
        self.save(f"resolved-citations-{label}", resolved)
        self.verify_citation(resolved[0]["chunk_id"])

    def followup(self):
        run = self.request("POST", "runs", json={"message": "在原来的部门范围和岗位要求上，更看重有量化检索评测结果、独立主导上线的经验。请重新比较，明确需要面试核实的项目。", "conversation_id": self.state["run"]["conversation_id"]})
        self.state["followup"] = run
        self.persist()
        assert run["turn_no"] == self.state["run"]["turn_no"] + 1 and run["conversation_id"] == self.state["run"]["conversation_id"]
        self.assert_completed(run, self.consume(run, "followup"), "followup")

    def hitl(self):
        run = self.request("POST", "runs", json={"message": "请只从不存在的部门 E2E_EMPTY_" + self.state["tag"] + " 筛选 Python 和企业知识库 RAG 工程师，按项目经历出评估报告。"})
        self.state["hitl_run"] = run
        self.persist()
        events = self.consume(run, "hitl-before")
        assert events[-1]["type"] == "hitl.required", events[-1]
        revision = f"只从部门为{self.state['department']}筛选 AI 应用工程师，要求 Python、企业知识库 RAG 和 Docker 项目经验，比较原文证据并给出推荐报告。"
        self.request("POST", f"runs/{run['run_id']}/resume", json={"value": revision})
        resumed = self.consume(run, "hitl-after", after=events[-1]["sequence"])
        assert resumed[0]["type"] == "hitl.resolved", resumed[0]
        self.assert_completed(run, events + resumed, "hitl")

    def cancel(self):
        run = self.request("POST", "runs", json={"message": "查找 AI 应用工程师并评估，取消测试。"})
        for _ in range(2):
            cancelled = self.request("DELETE", f"runs/{run['run_id']}")
            assert cancelled["status"] == "cancelled", cancelled
        history = self.request("GET", f"runs/history/{run['run_id']}")
        assert history["status"] == "cancelled", history
        self.check("cancellation", {"run_id": run["run_id"], "status": history["status"], "idempotent": True})

    def reports(self):
        for key, label in (("run", "evaluation"), ("followup", "followup"), ("hitl_run", "hitl")):
            if key in self.state:
                events = json.loads((self.output / f"events-{label if label != 'hitl' else 'hitl-after'}.json").read_text(encoding="utf-8"))
                if label == "hitl":
                    events = json.loads((self.output / "events-hitl-before.json").read_text(encoding="utf-8")) + events
                self.assert_completed(self.state[key], events, label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18080")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", default=time.strftime("%Y%m%d"))
    parser.add_argument("--stage", choices=["ingest", "wait_documents", "search", "run", "followup", "hitl", "cancel", "reports", "all"], default="all")
    args = parser.parse_args()
    verification = Verification(args.base, args.output, args.tag)
    for stage in (["ingest", "search", "run", "followup", "hitl", "cancel"] if args.stage == "all" else [args.stage]):
        getattr(verification, stage)()
    print("VERIFICATION PASSED: " + args.stage, flush=True)


if __name__ == "__main__":
    main()
