"""Jira orchestration with deterministic remote responses, never live credentials."""

import pytest
from typer.testing import CliRunner

from kcia.commands import jira as commands
from kcia.integrations import jira_cycle as jira
from kcia.main import app
from kcia.waves.session import Session, context_dir, session_path

real_transition = jira.transition


def issue(key, rank=1, category="new"):
    return jira.Issue(key=key, summary=f"Task {key}", description=f"Build {key}",
                      status="Open", category=category, priority="High", priority_rank=rank)


def snapshot(*children):
    return jira.Snapshot(parent=issue("PR-1"), subtasks=list(children), complete=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(jira, "transition", lambda *a: None)
    return tmp_path


def test_select_sorts_priority_and_excludes_done_and_delivered():
    result = jira.pending(snapshot(issue("PR-4", 2), issue("PR-3", 0), issue("PR-2", 0),
                                   issue("PR-5", 0, "done")), {"delivered": ["PR-3"]})
    assert [i.key for i in result] == ["PR-2", "PR-4"]


def test_selection_keeps_parent_and_siblings_but_resets_context(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2"), issue("PR-3")))
    context_dir(repo).mkdir(parents=True)
    (context_dir(repo) / "plan.md").write_text("old plan")
    session = commands.select(repo, key="PR-1", subtask="PR-2", profiles=["api"], scope=["src"])
    assert session.task["ticket_key"] == "PR-2"
    assert session.data["active_profiles"] == ["api"]
    assert session.task["scope"] == ["src"]
    assert not (context_dir(repo) / "plan.md").exists()
    context = (context_dir(repo) / "ticket.md").read_text()
    assert all(key in context for key in ["PR-1", "PR-2", "PR-3"])
    assert "ONLY the selected subtask" in context


def test_no_subtasks_runs_parent(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot())
    session = commands.select(repo, key="PR-1")
    assert session.task["ticket_key"] == "PR-1"
    commands.finish(repo, session)
    assert jira.load(repo)["phase"] == "complete"


def test_failed_start_resumes_same_selection(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2")))
    def fail(*args):
        raise jira.JiraError("offline")
    monkeypatch.setattr(jira, "transition", fail)
    with pytest.raises(jira.JiraError):
        commands.select(repo, key="PR-1", subtask="PR-2")
    assert jira.load(repo)["phase"] == "starting"
    assert Session.load(repo).task["ticket_key"] == "PR-2"
    monkeypatch.setattr(jira, "transition", lambda *a: None)
    commands.start(repo)
    assert jira.load(repo)["phase"] == "working"


def test_finish_is_idempotent_and_next_session_is_fresh(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2"), issue("PR-3")))
    first = commands.select(repo, key="PR-1", subtask="PR-2")
    commands.finish(repo, first)
    commands.finish(repo, first)
    assert jira.load(repo)["delivered"] == ["PR-2"]
    session_path(repo).unlink()
    second = commands.select(repo, subtask="PR-3")
    assert second.task["id"] != first.task["id"]
    assert all(w["status"] == "pending" for w in second.waves.values())


def test_parent_waits_for_remote_completion(repo, monkeypatch):
    cycle = {"parent": "PR-1", "active": None, "phase": "select", "delivered": ["PR-2"]}
    jira.save(repo, cycle)
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2", category="indeterminate")))
    calls = []
    monkeypatch.setattr(jira, "transition", lambda *a: calls.append(a))
    assert commands.select(repo) is None
    assert not calls
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2", category="done")))
    assert commands.select(repo) is None
    assert calls == [(repo, "PR-1", "parent_finish")]
    assert jira.load(repo)["phase"] == "complete"


def test_noninteractive_selection_pauses_even_with_yes(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-2")))
    monkeypatch.setattr("kcia.commands.work.atlassian_available", lambda *a: True)
    result = CliRunner().invoke(app, ["work", "PR-1", "--yes"])
    assert result.exit_code == 0, result.output
    assert "--subtask KEY" in result.output
    assert not session_path(repo).exists()
    executions = []
    monkeypatch.setattr("kcia.commands.work._execute", lambda session, **kw: executions.append(session))
    result = CliRunner().invoke(app, ["work", "--subtask", "PR-2"])
    assert result.exit_code == 0, result.output
    assert executions[0].task["ticket_key"] == "PR-2"


def test_fetch_rejects_partial_or_wrong_parent(repo, monkeypatch):
    data = snapshot(issue("PR-2")).model_dump()
    monkeypatch.setattr(jira, "request", lambda *a, **kw: data)
    data["complete"] = False
    with pytest.raises(jira.JiraError):
        jira.fetch(repo, "PR-1")
    data["complete"] = True
    with pytest.raises(jira.JiraError):
        jira.fetch(repo, "PR-99")


def test_transition_verifies_result(repo, monkeypatch):
    # Undo fixture's fake transition for this protocol test.
    monkeypatch.setattr(jira, "transition", real_transition)
    monkeypatch.setattr(jira, "settings", lambda *a: {"states": {"start": "Doing"}})
    calls = []
    def remote(*args, **kwargs):
        calls.append(kwargs)
        return {"key": "PR-1", "status": "Wrong", "category": "indeterminate"}
    monkeypatch.setattr(jira, "request", remote)
    with pytest.raises(jira.JiraError):
        jira.transition(repo, "PR-1", "start")
    assert calls == [{"transition": True}]


def test_done_retries_jira_without_repeating_git(repo, monkeypatch):
    from kcia.commands import commit
    session = Session.create(repo, text="PR-2", mode="ticket", ticket_key="PR-2")
    session.data.update(jira_cycle=True, done_checkpoint={"branch": "feature/p2", "title": "PR-2", "base": "main"})
    session.save()
    git_calls = []
    monkeypatch.setattr(commit, "_after_commit", lambda *a, **kw: git_calls.append(kw))
    monkeypatch.setattr(commands, "continue_cycle", lambda *a: None)
    def fail(*args):
        raise jira.JiraError("network down")
    monkeypatch.setattr(commands, "finish", fail)
    result = CliRunner().invoke(app, ["done", "--yes"])
    assert result.exit_code == 1
    assert len(git_calls) == 1
    monkeypatch.setattr(commands, "finish", lambda *a: None)
    result = CliRunner().invoke(app, ["done", "--yes"])
    assert result.exit_code == 0, result.output
    assert len(git_calls) == 1
    assert not session_path(repo).exists()


def test_dry_run_does_not_sync_pending_closure(repo, monkeypatch):
    session = Session.create(repo, text="PR-1", mode="ticket", ticket_key="PR-1")
    session.data["done_checkpoint"] = {"branch": "main", "title": "PR-1"}
    session.save()
    monkeypatch.setattr("kcia.commands.commit._complete_saved_done", lambda *a: pytest.fail("side effect"))
    result = CliRunner().invoke(app, ["done", "--dry-run"])
    assert result.exit_code == 0
    assert session_path(repo).exists()


def test_interactive_default_is_highest_priority(repo, monkeypatch):
    monkeypatch.setattr(jira, "fetch", lambda *a: snapshot(issue("PR-3", 3), issue("PR-2", 0)))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompts = []
    def choose(*args, **kwargs):
        prompts.append(kwargs)
        return kwargs["default"]
    monkeypatch.setattr(commands.typer, "prompt", choose)
    selected = commands.select(repo, key="PR-1")
    assert selected.task["ticket_key"] == "PR-2"
    assert prompts[0]["default"] == 1


def test_transition_tool_only_granted_to_sync(repo, monkeypatch):
    from types import SimpleNamespace
    from kcia.providers.base import RunResult
    monkeypatch.setattr(jira, "atlassian_available", lambda *a: True)
    monkeypatch.setattr(jira, "resolve_agents", lambda *a: {"planner": SimpleNamespace(provider="claude", model="test", effort=None)})
    monkeypatch.setattr(jira, "get_adapter", lambda *a: SimpleNamespace(locate=lambda: "/test/claude"))
    monkeypatch.setattr(jira, "render_claude_config", lambda *a: repo / "mcp.json")
    monkeypatch.setattr(jira, "allowed_tools_for_role", lambda *a: ["mcp__atlassian__getJiraIssue"])
    requests = []
    def runner(adapter, request):
        requests.append(request)
        return RunResult(output_text='{"ok":true}')
    monkeypatch.setattr(jira, "run_provider", runner)
    jira.request(repo, "read")
    jira.request(repo, "sync", transition=True)
    tool = "mcp__atlassian__transitionJiraIssue"
    assert tool not in requests[0].mcp_tools
    assert tool in requests[1].mcp_tools
    assert not requests[1].allow_edits
    assert "Bash" in requests[1].disallowed_tools


def test_git_steps_resume_after_a_failure(repo, monkeypatch):
    from kcia.commands import commit
    session = Session.create(repo, text="PR-2", mode="ticket", ticket_key="PR-2")
    session.data["done_checkpoint"] = {"branch": "feature/pr2", "title": "PR-2"}
    session.save()
    completed = []
    commit._run_step("step one", lambda: completed.append("one"), repo=repo)
    def fail():
        raise commit.GitError("offline")
    with pytest.raises(commit.GitError):
        commit._run_step("step two", fail, repo=repo)
    commit._run_step("step one", lambda: completed.append("duplicate"), repo=repo)
    commit._run_step("step two", lambda: completed.append("two"), repo=repo)
    assert completed == ["one", "two"]


def test_existing_task_is_not_overwritten(repo, monkeypatch):
    existing = Session.create(repo, text="existing", mode="prompt")
    monkeypatch.setattr(jira, "fetch", lambda *a: pytest.fail("must not contact Jira"))
    with pytest.raises(jira.JiraError):
        commands.select(repo, key="PR-1")
    assert Session.load(repo).task["id"] == existing.task["id"]
    assert jira.load(repo) is None
