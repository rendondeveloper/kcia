"""GitHub review protocol, isolated from workflow and Jira orchestration."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from kcia.git.repo import GH_BIN, GitError


def gh(repo: Path, *args: str, json_output: bool = True):
    result = subprocess.run([GH_BIN, *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise GitError(f"GitHub operation failed: {(result.stderr or result.stdout).strip()}")
    if not json_output:
        return result.stdout
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise GitError("GitHub returned invalid JSON.") from exc
    if isinstance(data, dict) and data.get("errors"):
        raise GitError(f"GitHub rejected the operation: {data['errors']}")
    return data


def status(repo: Path, url: str) -> dict:
    pr = gh(repo, "pr", "view", url, "--json",
            "id,url,state,isDraft,reviewDecision,mergeStateStatus,headRefName,baseRefName,"
            "headRefOid,statusCheckRollup")
    # gh's latestReviews shortcut omits IDs and commit OIDs; request them explicitly.
    pr["latestReviews"] = _connection(repo, url, pr["id"], "latestReviews",
                                      "id body state submittedAt commit{oid} author{login}")
    pr["comments"] = _connection(repo, url, pr["id"], "comments",
                                 "id body updatedAt author{login} viewerDidAuthor")
    return pr


def _connection(repo: Path, url: str, node: str, field: str, fields: str) -> list[dict]:
    query = ("query($id:ID!,$cursor:String){node(id:$id){... on PullRequest{"
             + field + "(first:100,after:$cursor){pageInfo{hasNextPage endCursor}nodes{"
             + fields + "}}}}}")
    variables = {"id": node}
    found = []
    while True:
        connection = _graphql(repo, url, query, **variables)["node"][field]
        found.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return found
        variables["cursor"] = connection["pageInfo"]["endCursor"]


def _graphql(repo: Path, url: str, query: str, **variables) -> dict:
    host = urlparse(url).hostname
    if not host:
        raise GitError("Invalid PR URL.")
    args = ["api", "graphql", "--hostname", host, "-f", f"query={query}"]
    for key, value in variables.items():
        args += ["-F" if isinstance(value, int) else "-f", f"{key}={value}"]
    return gh(repo, *args)["data"]


def threads(repo: Path, url: str) -> list[dict]:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) != 4 or parts[2] != "pull" or not parts[3].isdigit():
        raise GitError("Invalid PR URL.")
    query = '''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
      repository(owner:$owner,name:$name){pullRequest(number:$number){
        reviewThreads(first:100,after:$cursor){
          pageInfo{hasNextPage endCursor}
          nodes{id isResolved path line comments(first:100){
            pageInfo{hasNextPage endCursor}
            nodes{id body updatedAt author{login}}
          }}
        }
      }}
    }'''
    found = []
    variables = dict(owner=parts[0], name=parts[1], number=int(parts[3]))
    while True:
        connection = _graphql(repo, url, query, **variables)["repository"]["pullRequest"]["reviewThreads"]
        for thread in connection["nodes"]:
            comments = thread["comments"]
            all_comments = list(comments["nodes"])
            while comments["pageInfo"]["hasNextPage"]:
                comments = _graphql(repo, url, '''query($id:ID!,$cursor:String!){
                  node(id:$id){... on PullRequestReviewThread{comments(first:100,after:$cursor){
                    pageInfo{hasNextPage endCursor} nodes{id body updatedAt author{login}}
                  }}}}''', id=thread["id"], cursor=comments["pageInfo"]["endCursor"])["node"]["comments"]
                all_comments.extend(comments["nodes"])
            thread["comments"] = all_comments
            if not thread["isResolved"]:
                found.append(thread)
        if not connection["pageInfo"]["hasNextPage"]:
            return found
        variables["cursor"] = connection["pageInfo"]["endCursor"]


def fingerprint(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()


def observations(pr: dict, review_threads: list[dict]) -> list[dict]:
    items = [{"id": t["id"], "thread": True, "content": t} for t in review_threads]
    for review in pr.get("latestReviews", []):
        if review.get("state") in {"CHANGES_REQUESTED", "COMMENTED"} and review.get("body", "").strip():
            items.append({"id": review["id"], "thread": False, "content": review})
    for comment in pr.get("comments", []):
        if not comment.get("viewerDidAuthor") and comment.get("body", "").strip():
            items.append({"id": comment["id"], "thread": False, "content": comment})
    return items


def approved(pr: dict) -> bool:
    if pr.get("state") != "OPEN" or pr.get("isDraft"):
        return False
    if pr.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        return False
    if pr.get("mergeStateStatus") not in {"CLEAN", "HAS_HOOKS"}:
        return False
    reviews = pr.get("latestReviews", [])
    if any(r.get("state") == "CHANGES_REQUESTED" for r in reviews):
        return False
    if not any(r.get("state") == "APPROVED" and (r.get("commit") or {}).get("oid") == pr.get("headRefOid")
               for r in reviews):
        return False
    for check in pr.get("statusCheckRollup") or []:
        if check.get("__typename") == "CheckRun":
            if check.get("status") != "COMPLETED" or check.get("conclusion") not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                return False
        elif check.get("state") != "SUCCESS":
            return False
    return True


def request_reviewers(repo: Path, pr_url: str, reviewers: tuple[str, ...] | list[str]) -> None:
    """Ask GitHub to request (or re-request) review from each configured identifier."""
    ordered = tuple(reviewers)
    if not ordered:
        return
    args = ["pr", "edit", pr_url]
    for handle in ordered:
        args.extend(["--add-reviewer", handle])
    gh(repo, *args, json_output=False)


def merge(repo: Path, url: str, head: str) -> None:
    # GitHub rejects a changed head and enforces branch protection; never --admin.
    gh(repo, "pr", "merge", url, "--merge", "--match-head-commit", head, json_output=False)


def resolve(repo: Path, url: str, thread_id: str) -> None:
    result = _graphql(repo, url, '''mutation($id:ID!){
      resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}
    }''', id=thread_id)["resolveReviewThread"]["thread"]
    if result.get("id") != thread_id or not result.get("isResolved"):
        raise GitError("Could not verify review thread resolution.")
