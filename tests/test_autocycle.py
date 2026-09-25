"""Autonomous selection, review and merge recovery without live remote effects."""
import json
from copy import deepcopy

import pytest
from typer.testing import CliRunner

from kcia.commands import cycle
from kcia.commands import jira as commands
from kcia.git.repo import GitError
from kcia.integrations import github_reviews as github
from kcia.integrations import jira_cycle as jira
from kcia.main import app
from kcia.waves.session import Session, session_path


@pytest.fixture
def session(tmp_path):
    task = Session.create(tmp_path, text="PR-2", mode="ticket", ticket_key="PR-2")
    task.task.update(branch="feature/pr2", base_branch="develop")
    task.data.update(jira_autocycle=True, jira_cycle=True, done_checkpoint={
        "pull_request_url": "https://github.com/owner/repo/pull/1", "awaiting_pr_approval": True,
    })
    task.save()
    return task


@pytest.fixture
def pr():
    return {
        "url": "https://github.com/owner/repo/pull/1", "state": "OPEN", "isDraft": False,
        "reviewDecision": "APPROVED", "mergeStateStatus": "CLEAN",
        "headRefOid": "new", "headRefName": "feature/pr2", "baseRefName": "develop",
        "latestReviews": [{"id": "r1", "state": "APPROVED", "commit": {"oid": "new"}}],
        "statusCheckRollup": [],
    }


def item(key, labels=None, rank=0):
    return jira.Issue(key=key, summary=key, description=key, status="Open", category="new",
                      priority="High", priority_rank=rank, labels=labels or [])


def test_label_is_required_on_the_selected_child(tmp_path, monkeypatch):
    snap = jira.Snapshot(parent=item("PR-1", ["kcia"]), complete=True,
                         subtasks=[item("PR-2", rank=0), item("PR-3", ["kcia"], rank=2)])
    monkeypatch.setattr(jira, "fetch", lambda *a: snap)
    monkeypatch.setattr(jira, "transition", lambda *a: None)
    selected = commands.select(tmp_path, key="PR-1", auto=True, required_label="kcia")
    assert selected.task["ticket_key"] == "PR-3"
    assert selected.data["jira_autocycle"]
    assert "PR-2" in (tmp_path / ".ai/context/ticket.md").read_text()


def test_labeled_parent_does_not_authorize_unlabeled_children(tmp_path, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: jira.Snapshot(
        parent=item("PR-1", ["kcia"]), complete=True, subtasks=[item("PR-2")]))
    monkeypatch.setattr(jira, "transition", lambda *a: pytest.fail("must not transition"))
    assert commands.select(tmp_path, key="PR-1", auto=True, required_label="kcia") is None
    assert not session_path(tmp_path).exists()


def test_search_filters_label_and_project(tmp_path, monkeypatch):
    monkeypatch.setattr(jira, "settings", lambda *a: {"project_keys": ["PR"]})
    monkeypatch.setattr(jira, "request", lambda *a, **kw: {
        "complete": True, "issues": [i.model_dump() for i in [
            item("PR-1"), item("OTHER-1", ["kcia"]), item("PR-2", ["kcia"])]]})
    assert [i.key for i in jira.search_labeled(tmp_path)] == ["PR-2"]


@pytest.mark.parametrize("change", [
    {"isDraft": True}, {"state": "CLOSED"}, {"reviewDecision": "CHANGES_REQUESTED"},
    {"reviewDecision": "REVIEW_REQUIRED"}, {"mergeStateStatus": "BLOCKED"},
    {"latestReviews": []},
    {"latestReviews": [{"state": "APPROVED", "commit": {"oid": "old"}}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "IN_PROGRESS"}]},
    {"statusCheckRollup": [{"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}]},
    {"statusCheckRollup": [{"__typename": "StatusContext", "state": "PENDING"}]},
])
def test_merge_waits_for_current_approval_and_checks(pr, change):
    pr.update(change)
    assert not github.approved(pr)


def test_unprotected_branch_still_requires_current_approval(pr):
    pr["reviewDecision"] = ""
    assert github.approved(pr)
    pr["latestReviews"] = []
    assert not github.approved(pr)


def test_merge_pins_the_reviewed_commit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(github, "gh", lambda *a, **kw: calls.append((a, kw)))
    github.merge(tmp_path, "url", "sha")
    assert "--match-head-commit" in calls[0][0]
    assert "sha" in calls[0][0]
    assert "--admin" not in calls[0][0]


def test_queue_acceptance_does_not_close_jira(session, pr, monkeypatch):
    calls = []
    monkeypatch.setattr(cycle, "is_dirty", lambda *a: False)
    monkeypatch.setattr(cycle, "current_branch", lambda *a: "feature/pr2")
    monkeypatch.setattr(cycle, "run_git", lambda *a: "new")
    monkeypatch.setattr(github, "status", lambda *a: pr)
    monkeypatch.setattr(github, "threads", lambda *a: [])
    monkeypatch.setattr(github, "merge", lambda *a: calls.append("merge"))
    monkeypatch.setattr(cycle, "_close_after_merge", lambda *a: pytest.fail("not merged yet"))
    assert cycle._handle_waiting_pr(session.repo_root, session) is False
    assert calls == ["merge"]


def test_closed_pr_does_not_close_jira(session, pr, monkeypatch):
    pr["state"] = "CLOSED"
    monkeypatch.setattr(github, "status", lambda *a: pr)
    with pytest.raises(GitError, match="closed without merge"):
        cycle._handle_waiting_pr(session.repo_root, session)


def test_merge_recovery_does_not_repeat_jira_finish(session, monkeypatch):
    calls = []
    monkeypatch.setattr(commands, "finish", lambda *a: calls.append("done"))
    monkeypatch.setattr(cycle, "is_dirty", lambda *a: False)
    monkeypatch.setattr("kcia.commands.commit._remote_name", lambda *a: "origin")
    def fail(*args):
        if "pull" in args:
            raise GitError("network down")
    monkeypatch.setattr(cycle, "run_git", fail)
    with pytest.raises(GitError):
        cycle._close_after_merge(session.repo_root, session)
    assert Session.load(session.repo_root).data["done_checkpoint"]["jira_finished"]
    monkeypatch.setattr(cycle, "run_git", lambda *a: "")
    cycle._close_after_merge(session.repo_root, Session.load(session.repo_root))
    assert calls == ["done"]
    assert not session_path(session.repo_root).exists()


def test_merged_transition_requires_done_even_in_pr_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(jira, "settings", lambda *a: {"states": {"finish": "In Review"}})
    monkeypatch.setattr(jira, "request", lambda *a, **kw: {
        "key": "PR-1", "status": "In Review", "category": "indeterminate"})
    with pytest.raises(jira.JiraError):
        jira.transition(tmp_path, "PR-1", "merged")
    monkeypatch.setattr(jira, "settings", lambda *a: {})
    monkeypatch.setattr(jira, "request", lambda *a, **kw: {
        "key": "PR-1", "status": "Done", "category": "done"})
    jira.transition(tmp_path, "PR-1", "merged")


def test_review_restarts_plan_and_profile_implementation(session, pr, monkeypatch):
    monkeypatch.setattr(cycle, "is_dirty", lambda *a: False)
    monkeypatch.setattr(cycle, "current_branch", lambda *a: "feature/pr2")
    monkeypatch.setattr(cycle, "run_git", lambda *a: "new")
    session.data["profile_runs"] = {"api": {"waves": {"implementation": {"status": "completed"}}}}
    for state in session.waves.values():
        state["status"] = "completed"
    observation = {"id": "t1", "thread": True, "content": {"body": "Fix null handling"}}
    cycle._begin_review(session, pr, [observation])
    assert "done_checkpoint" not in session.data
    assert session.wave_status("analysis") == "pending"
    assert session.wave_status("implementation") == "pending"
    assert not session.data["profile_runs"]
    assert "Fix null handling" in session.data["injections"][-1]
    with pytest.raises(GitError, match="evidence"):
        cycle._repair_evidence(session)


def test_resolve_only_after_push_and_unchanged_conversation(session, pr, monkeypatch):
    thread = {"id": "t1", "isResolved": False, "comments": [{"body": "Fix null"}]}
    session.data["review_repair"] = {"url": pr["url"], "head": "old", "phase": "pushed",
        "resolved": [], "observations": [{"id": "t1", "thread": True, "content": thread}]}
    monkeypatch.setattr(github, "status", lambda *a: pr)
    monkeypatch.setattr(cycle, "run_git", lambda *a: "old")
    monkeypatch.setattr(github, "resolve", lambda *a: pytest.fail("premature resolve"))
    with pytest.raises(GitError, match="committed and pushed"):
        cycle._finish_review(session.repo_root, session)
    monkeypatch.setattr(cycle, "run_git", lambda *a: "new")
    changed = deepcopy(thread)
    changed["comments"].append({"body": "Also fix another case"})
    monkeypatch.setattr(github, "threads", lambda *a: [changed])
    with pytest.raises(GitError, match="changed during repair"):
        cycle._finish_review(session.repo_root, session)
    monkeypatch.setattr(github, "threads", lambda *a: [thread])
    calls = []
    monkeypatch.setattr(github, "resolve", lambda *a: calls.append(a[-1]))
    cycle._finish_review(session.repo_root, session)
    assert calls == ["t1"]
    assert session.data["review_repair"]["phase"] == "complete"


def test_process_lock_is_project_scoped(tmp_path):
    with cycle._cycle_lock(tmp_path):
        with pytest.raises(GitError, match="already running"):
            with cycle._cycle_lock(tmp_path):
                pass
        with cycle._cycle_lock(tmp_path / "other"):
            pass
    with cycle._cycle_lock(tmp_path):
        pass


def test_status_does_not_write_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle, "load_repo", lambda: tmp_path)
    result = CliRunner().invoke(app, ["cycle", "jira", "--status"])
    assert result.exit_code == 0, result.output
    assert not cycle.config_path(tmp_path).exists()


def test_review_threads_paginates_outer_and_inner(tmp_path, monkeypatch):
    calls = []
    page_end = {"hasNextPage": False, "endCursor": None}
    def query(repo, url, text, **variables):
        calls.append(variables)
        if variables.get("id") == "t1":
            return {"node": {"comments": {"nodes": [{"id": "c2"}], "pageInfo": page_end}}}
        if variables.get("cursor") == "next-thread":
            nodes = [{"id": "t2", "isResolved": False, "comments": {"nodes": [], "pageInfo": page_end}}]
            page = page_end
        else:
            nodes = [{"id": "t1", "isResolved": False, "comments": {
                "nodes": [{"id": "c1"}], "pageInfo": {"hasNextPage": True, "endCursor": "next-comment"}}}]
            page = {"hasNextPage": True, "endCursor": "next-thread"}
        return {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes, "pageInfo": page}}}}
    monkeypatch.setattr(github, "_graphql", query)
    threads = github.threads(tmp_path, "https://github.com/owner/repo/pull/1")
    assert [t["id"] for t in threads] == ["t1", "t2"]
    assert [c["id"] for c in threads[0]["comments"]] == ["c1", "c2"]
    assert len(calls) == 3


def test_continuous_loop_advances_only_after_merge(tmp_path, monkeypatch):
    events = []
    queue = ["PR-1", "PR-2"]
    def start(repo, config):
        key = queue.pop(0)
        task = Session.create(repo, text=key, mode="ticket", ticket_key=key)
        task.data["jira_autocycle"] = True
        task.save()
        events.append(("start", key))
        return task
    def run(repo, task, **kwargs):
        events.append(("workflow-and-pr", task.task["ticket_key"]))
        task.data["done_checkpoint"] = {"awaiting_pr_approval": True}
        task.save()
    def waiting(repo, task):
        events.append(("merged", task.task["ticket_key"]))
        session_path(repo).unlink()
        return True
    monkeypatch.setattr(cycle, "_start_next_issue", start)
    monkeypatch.setattr(cycle, "_run_session", run)
    monkeypatch.setattr(cycle, "_handle_waiting_pr", waiting)
    cycle._cycle_loop(tmp_path, cycle.JiraCycleConfig(base_branch="develop"),
                      loop=True, max_items=2, quiet=True)
    assert events == [("start", "PR-1"), ("workflow-and-pr", "PR-1"), ("merged", "PR-1"),
                      ("start", "PR-2"), ("workflow-and-pr", "PR-2"), ("merged", "PR-2")]


def test_run_session_validates_before_commit_and_resolution(session, monkeypatch):
    session.data.pop("done_checkpoint")
    session.data["review_repair"] = {"phase": "working", "observations": [{"id": "t1"}]}
    session.save()
    events = []
    monkeypatch.setattr(commands, "start", lambda *a: None)
    def execute(task, **kwargs):
        assert kwargs["yes"] is True
        events.append("workflow-with-validation")
        for state in task.waves.values():
            state["status"] = "completed"
        task.save()
        (task.repo_root / ".ai/local/pr-review-result.json").write_text(json.dumps({
            "addressed": [{"id": "t1", "evidence": "fixed parser.py; test_null passed"}]}))
    def commit(*args):
        events.append("commit-and-push")
        saved = Session.load(session.repo_root)
        saved.data["done_checkpoint"] = {"awaiting_pr_approval": True}
        saved.save()
    monkeypatch.setattr(cycle, "_execute", execute)
    monkeypatch.setattr(cycle, "commit_command", commit)
    monkeypatch.setattr(cycle, "_finish_review", lambda *a: events.append("resolve"))
    cycle._run_session(session.repo_root, session, quiet=True)
    assert events == ["workflow-with-validation", "commit-and-push", "resolve"]


def test_review_validation_failure_never_pushes_or_resolves(session, monkeypatch):
    import typer
    session.data.pop("done_checkpoint")
    monkeypatch.setattr(commands, "start", lambda *a: None)
    def fail(*args, **kwargs):
        raise typer.Exit(code=1)
    monkeypatch.setattr(cycle, "_execute", fail)
    monkeypatch.setattr(cycle, "commit_command", lambda *a: pytest.fail("validation failed"))
    monkeypatch.setattr(cycle, "_finish_review", lambda *a: pytest.fail("validation failed"))
    with pytest.raises(typer.Exit):
        cycle._run_session(session.repo_root, session, quiet=True)


def test_local_jira_ignores_the_coding_fallback_pin(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kcia.config import AgentSetting, ResolvedAgent
    from kcia.providers.base import RunResult, ProviderCapabilities
    route = ResolvedAgent(role="planner", provider="claude", model="primary", effort=None,
                          origin="repo", active_index=1, automatic=True,
                          fallbacks=(AgentSetting(provider="cursor", model="fallback"),))
    monkeypatch.setattr(jira, "resolve_agents", lambda *a: {"planner": route})
    monkeypatch.setattr(jira, "atlassian_available", lambda *a: True)
    adapter = SimpleNamespace(locate=lambda: "claude", capabilities=ProviderCapabilities(
        supports_streaming=True, supports_sessions=False, supports_effort=False,
        supports_tool_restriction=True, supports_mcp_config=True))
    monkeypatch.setattr(jira, "get_adapter", lambda *a: adapter)
    monkeypatch.setattr("kcia.execution.coordinator.get_adapter", lambda *a: adapter)
    monkeypatch.setattr(jira, "allowed_tools_for_role", lambda *a: [])
    monkeypatch.setattr(jira, "_mcp_config", lambda *a: tmp_path / "mcp.json")
    models = []
    def run(adapter, request, **kwargs):
        models.append(request.model)
        return RunResult(output_text='{"ok":true}')
    monkeypatch.setattr(jira, "run_provider", run)
    with jira.local_connection():
        assert jira.request(tmp_path, "read") == {"ok": True}
    assert models == ["primary"]
    assert route.active_index == 1
