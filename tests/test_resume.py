"""Resumable runs: progress is checkpointed, and a resume re-runs only what had not finished. No model calls."""

from datetime import date

from matcher.agents import alignment, leader, research, verification
from matcher.agents.research import research_summary
from matcher.checkpoint import open_checkpointer
from matcher.context import Context
from matcher.graph import build_graph
from matcher.schemas import Check, Decision, LanePlan, SearchBrief, VerificationResult
from matcher.trace import Tracer

from .conftest import make_posting
from .test_orchestration import _alignment


def test_resume_reruns_only_the_lane_search_that_failed(monkeypatch, tmp_path, profile, thesis):
    calls, network = [], {"up": False}

    def search_lane(ctx, brief, lane, feedback, seen, off):
        calls.append(lane.lane_id)
        if lane.lane_id == "hedge_funds" and not network["up"]:
            raise ConnectionError("network dropped")
        return [make_posting(title=f"{lane.lane_id} role", employer=lane.lane_id, source_id=f"{lane.lane_id} | role")]

    def review(ctx, i, p, v, a, lane=None):
        return Decision(iteration=i, posting_id=p.source_id, title=p.title, employer=p.employer, verdict="reject",
                        gates={"verified": True, "wanted": False, "attainable": True}, rationale="", feedback="",
                        lane=lane, tier="below", role_type=p.role_type, desirability=10, attainability_pct=80, rank_score=10)

    lanes = ["exchanges_venues", "hedge_funds", "research"]
    brief = SearchBrief(lanes=[LanePlan(lane_id=x, rationale="", target_roles=[], target_employers=[], search_queries=[])
                               for x in lanes], must_haves=[], red_flags=[])
    monkeypatch.setattr(research, "search_lane", search_lane)
    monkeypatch.setattr(verification, "verify", lambda ctx, p: VerificationResult(
        passed=True, checks=[Check(name="ok", passed=True, evidence="", source="rule")]))
    monkeypatch.setattr(alignment, "assess", lambda ctx, p: _alignment("below"))
    monkeypatch.setattr(leader, "plan", lambda ctx: brief)
    monkeypatch.setattr(leader, "review", review)
    settings = {"source": "live", "max_iterations": 16, "max_search_rounds": 1, "min_reviews": 3, "models": {}, "backend": "api"}
    ctx = Context(None, Tracer(tmp_path), profile, thesis, settings, date(2026, 10, 11), tmp_path)
    config = {"configurable": {"thread_id": "run1"}, "recursion_limit": 100}

    try:
        build_graph(ctx, open_checkpointer(tmp_path / "ck.sqlite")).invoke({}, config)
        raise AssertionError("expected the first attempt to fail")
    except ConnectionError:
        pass
    assert sorted(calls) == sorted(lanes)

    calls.clear()
    network["up"] = True
    state = build_graph(ctx, open_checkpointer(tmp_path / "ck.sqlite")).invoke(None, config)  # as a new process would
    assert calls == ["hedge_funds"]  # the two lanes that finished are not searched (or paid for) again
    assert sorted(d.lane for d in state["decisions"]) == sorted(lanes)


def test_tracer_keeps_whole_run_totals_when_resumed(tmp_path):
    class R:
        cost_usd, input_tokens, output_tokens, web_searches, text = 0.5, 100, 10, 2, ""
    first = Tracer(tmp_path)
    first.llm_call("research", "m", "p", R(), 1.0)
    resumed = Tracer(tmp_path)
    assert (resumed.calls, resumed.total_cost_usd, resumed.web_searches) == (1, 0.5, 2)


def test_research_summary_line_is_found():
    assert research_summary("notes...\n**SUMMARY: found 0; most roles closed or start ASAP**") == \
        "SUMMARY: found 0; most roles closed or start ASAP"
    assert research_summary("no summary here") is None


def test_research_uses_basic_web_tools_and_reports_tool_errors(monkeypatch):
    from types import SimpleNamespace as NS

    from matcher import llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    backend = llm.ApiBackend()
    sent = {}
    blocks = [NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
              NS(type="web_search_tool_result", content=[NS(url="https://x")]),
              NS(type="web_fetch_tool_result", content=NS(error_code="url_not_accessible")),
              NS(type="text", text="notes")]
    usage = NS(input_tokens=1000, output_tokens=100, server_tool_use=NS(web_search_requests=2))
    def create(**kw):
        sent.update(kw)
        return NS(usage=usage, stop_reason="end_turn", content=blocks, stop_details=None)
    monkeypatch.setattr(backend.client.messages, "create", create)
    result = backend.research("claude-sonnet-5-5", "s", "p", {"tool_version": "basic"})
    assert [t["type"] for t in sent["tools"]] == ["web_search_20250305", "web_fetch_20250910"]
    assert sent["tools"][0]["max_uses"] == 8 and sent["tools"][1]["max_content_tokens"] == 8000
    assert result.tool_errors == ["web_search:max_uses_exceeded", "web_fetch:url_not_accessible"]
    assert result.text == "notes" and result.web_searches == 2
