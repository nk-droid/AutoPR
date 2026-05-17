import argparse
from pathlib import Path
from core.contracts.enums import (
    GitHubIssuePickStrategy,
    GitHubIssueSort,
    GitHubIssueState,
    GitHubSortDirection,
)
from infra.github.client import GitHubAPIError, GitHubClient
from infra.github.issues import get_issues, get_and_pick_issue, get_issue_details
from infra.repo_worker.git_utils import GitService

def _normalize_text(value: str) -> str:
    return value.strip()

def _validate_repo_name(repo: str) -> str:
    normalized = _normalize_text(repo)
    if normalized.count("/") != 1 or normalized.startswith("/") or normalized.endswith("/"):
        raise ValueError("Invalid --repo value. Expected owner/repo format.")
    return normalized

def _normalize_pr_head(head: str, *, head_owner: str = "") -> str:
    normalized_head = _normalize_text(head)
    if not normalized_head:
        raise ValueError("Missing --head value.")
    owner = _normalize_text(head_owner)
    if owner and ":" in normalized_head:
        raise ValueError("Use either --head owner:branch or --head-owner, not both.")
    if owner:
        return f"{owner}:{normalized_head}"
    return normalized_head

def _format_pr_create_hints(exc: GitHubAPIError) -> str:
    if exc.status_code != 422:
        return ""
    hints = [
        "Verify the head branch exists on remote and has been pushed.",
        "Ensure head is ahead of base (GitHub rejects PRs with no commits between branches).",
        "If the source branch is in a fork, use --head owner:branch or pass --head-owner.",
        "If both repos are in the same organization but different repos, pass --head-repo.",
    ]
    return "\nHints:\n- " + "\n- ".join(hints)

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AutoPR Git/GitHub operations scaffold")
    parser.add_argument("--repo-path", default=".", help="Local repository path (default: current dir)")
    subparsers = parser.add_subparsers(dest="command")
    status = subparsers.add_parser("status", help="Show git status")
    status.add_argument("--short", action="store_true", help="Use short status output")
    checkout = subparsers.add_parser("checkout", help="Checkout a branch")
    checkout.add_argument("branch", help="Branch to checkout")
    checkout.add_argument("--create", action="store_true", help="Create the branch if needed")
    branch_delete = subparsers.add_parser("branch-delete", help="Delete a branch")
    branch_delete.add_argument("branch", help="Branch to delete")
    branch_delete.add_argument("--force", action="store_true", help="Force delete local branch")
    branch_delete.add_argument("--remote", default=None, help="Remote name for optional remote delete")
    branch_delete.add_argument(
        "--delete-remote",
        action="store_true",
        help="Also delete the branch from remote",
    )
    pull = subparsers.add_parser("pull", help="Pull updates from a remote")
    pull.add_argument("--remote", default="origin", help="Remote name")
    pull.add_argument("--branch", default=None, help="Branch to pull (default: remote default)")
    pull.add_argument("--rebase", action="store_true", help="Use --rebase")
    push = subparsers.add_parser("push", help="Push updates to a remote")
    push.add_argument("--remote", default="origin", help="Remote name")
    push.add_argument("--branch", default=None, help="Branch to push (default: current branch)")
    push.add_argument("--set-upstream", action="store_true", help="Use -u on push")
    commit = subparsers.add_parser("commit", help="Create a commit")
    commit.add_argument("-m", "--message", required=True, help="Commit message")
    commit.add_argument("--stage-all", action="store_true", help="Run git add . before commit")
    commit.add_argument(
        "--add",
        action="append",
        default=[],
        help="Specific path to stage (can be repeated)",
    )
    commit.add_argument(
        "--all-files",
        action="store_true",
        help="Use git commit -a for tracked files",
    )
    pr_create = subparsers.add_parser("pr-create", help="Create a GitHub pull request")
    pr_create.add_argument("--repo", required=True, help="owner/repo format")
    pr_create.add_argument("--title", required=True, help="PR title")
    pr_create.add_argument("--head", required=True, help="Head branch")
    pr_create.add_argument(
        "--head-owner",
        default="",
        help="Optional owner for fork PRs; transforms head to owner:branch",
    )
    pr_create.add_argument(
        "--head-repo",
        default="",
        help="Optional source repository name for same-organization cross-repo PRs",
    )
    pr_create.add_argument("--base", required=True, help="Base branch")
    pr_create.add_argument("--body", default="", help="PR body")
    pr_create.add_argument("--draft", action="store_true", help="Create a draft PR")
    pr_create.add_argument("--token", default=None, help="GitHub token override")
    issues_list = subparsers.add_parser("issues-list", help="List repository issues")
    issues_list.add_argument("--repo", required=True, help="owner/repo format")
    issues_list.add_argument(
        "--state",
        default=GitHubIssueState.OPEN.value,
        choices=[item.value for item in GitHubIssueState],
        help="Issue state",
    )
    issues_list.add_argument("--labels", default=None, help="Comma-separated labels")
    issues_list.add_argument("--per-page", type=int, default=30, help="Number of issues per page")
    issues_list.add_argument("--page", type=int, default=1, help="Issues page number")
    issues_list.add_argument(
        "--sort",
        default=GitHubIssueSort.CREATED.value,
        choices=[item.value for item in GitHubIssueSort],
        help="Sort field",
    )
    issues_list.add_argument(
        "--direction",
        default=GitHubSortDirection.ASC.value,
        choices=[item.value for item in GitHubSortDirection],
        help="Sort direction",
    )
    issues_list.add_argument("--token", default=None, help="GitHub token override")
    issue_pick = subparsers.add_parser("issue-pick", help="Pick one issue from repository")
    issue_pick.add_argument("--repo", required=True, help="owner/repo format")
    issue_pick.add_argument(
        "--state",
        default=GitHubIssueState.OPEN.value,
        choices=[item.value for item in GitHubIssueState],
        help="Issue state",
    )
    issue_pick.add_argument("--labels", default=None, help="Comma-separated labels")
    issue_pick.add_argument("--per-page", type=int, default=30, help="Number of issues per page")
    issue_pick.add_argument("--page", type=int, default=1, help="Issues page number")
    issue_pick.add_argument(
        "--strategy",
        default=GitHubIssuePickStrategy.OLDEST_OPEN.value,
        choices=[item.value for item in GitHubIssuePickStrategy],
        help="Issue pick strategy",
    )
    issue_pick.add_argument("--token", default=None, help="GitHub token override")
    issue_details = subparsers.add_parser("issue-details", help="Get issue details by number or URL")
    issue_details.add_argument(
        "--issue",
        required=True,
        help="Issue reference (e.g. 123, #123, or full GitHub issue URL)",
    )
    issue_details.add_argument("--repo", default=None, help="owner/repo (required for numeric issue)")
    issue_details.add_argument("--comments-limit", type=int, default=20, help="Number of comments to fetch")
    issue_details.add_argument("--comments-page", type=int, default=1, help="Comments page number")
    issue_details.add_argument("--token", default=None, help="GitHub token override")
    return parser

def _git(args: argparse.Namespace) -> GitService:
    return GitService(Path(args.repo_path))

def _run_status(args: argparse.Namespace) -> int:
    print(_git(args).status(short=args.short))
    return 0

def _run_checkout(args: argparse.Namespace) -> int:
    branch = _git(args).checkout_branch(args.branch, create=args.create)
    print(f"Checked out {branch}")
    return 0

def _run_pull(args: argparse.Namespace) -> int:
    output = _git(args).pull(remote=args.remote, branch=args.branch, rebase=args.rebase)
    print(output)
    return 0

def _run_branch_delete(args: argparse.Namespace) -> int:
    output = _git(args).delete_branch(
        branch=args.branch,
        force=args.force,
        remote=args.remote,
        delete_remote=args.delete_remote,
    )
    print(output)
    return 0

def _run_push(args: argparse.Namespace) -> int:
    output = _git(args).push(
        remote=args.remote,
        branch=args.branch,
        set_upstream=args.set_upstream,
    )
    print(output)
    return 0

def _run_commit(args: argparse.Namespace) -> int:
    git = _git(args)
    if args.stage_all:
        git.add(".")
    if args.add:
        git.add(*args.add)
    output = git.commit(args.message, all_files=args.all_files)
    print(output)
    return 0

def _run_pr_create(args: argparse.Namespace) -> int:
    repo = _validate_repo_name(args.repo)
    title = _normalize_text(args.title)
    if not title:
        raise ValueError("Missing --title value.")
    head = _normalize_pr_head(args.head, head_owner=args.head_owner)
    base = _normalize_text(args.base)
    if not base:
        raise ValueError("Missing --base value.")
    head_repo = _normalize_text(args.head_repo) or None
    client = GitHubClient(token=args.token)
    try:
        pr = client.create_pull_request(
            repo=repo,
            title=title,
            head=head,
            head_repo=head_repo,
            base=base,
            body=args.body,
            draft=args.draft,
        )
    except GitHubAPIError as exc:
        raise ValueError(f"{exc}{_format_pr_create_hints(exc)}") from exc
    finally:
        client.close()
    number = pr.get("number", "unknown")
    url = pr.get("html_url", "")
    print(f"Created PR #{number} {url}".strip())
    return 0

def _run_issues_list(args: argparse.Namespace) -> int:
    issues = get_issues(
        args.repo,
        state=args.state,
        labels=args.labels,
        per_page=args.per_page,
        page=args.page,
        sort=args.sort,
        direction=args.direction,
        token=args.token,
    )
    if not issues:
        print("No issues found")
        return 0
    for issue in issues:
        number = issue.get("number", "unknown")
        title = issue.get("title", "")
        state = issue.get("state", "")
        url = issue.get("html_url", "")
        print(f"#{number} [{state}] {title} {url}".strip())
    return 0

def _run_issue_pick(args: argparse.Namespace) -> int:
    issue = get_and_pick_issue(
        args.repo,
        strategy=args.strategy,
        state=args.state,
        labels=args.labels,
        per_page=args.per_page,
        page=args.page,
        token=args.token,
    )
    number = issue.get("number", "unknown")
    title = issue.get("title", "")
    state = issue.get("state", "")
    url = issue.get("html_url", "")
    print(f"Picked issue #{number} [{state}] {title} {url}".strip())
    return 0

def _run_issue_details(args: argparse.Namespace) -> int:
    issue = get_issue_details(
        args.issue,
        repo=args.repo,
        token=args.token,
        include_comments=True,
        comments_per_page=args.comments_limit,
        comments_page=args.comments_page,
    )
    number = issue.get("number", "unknown")
    title = issue.get("title", "")
    state = issue.get("state", "")
    url = issue.get("html_url", "")
    labels = issue.get("labels", [])
    label_names = [label.get("name", "") for label in labels if isinstance(label, dict)]
    assignee = issue.get("assignee") or {}
    assignee_login = assignee.get("login", "")
    print(f"Issue #{number} [{state}] {title}")
    print(f"URL: {url}")
    if label_names:
        print(f"Labels: {', '.join(label_names)}")
    if assignee_login:
        print(f"Assignee: {assignee_login}")
    body = (issue.get("body") or "").strip()
    print("Body:")
    if body:
        print(body)
    else:
        print("(empty)")
    comments = issue.get("comments_items", [])
    print(f"Comments ({len(comments)} shown):")
    if not comments:
        print("(none)")
        return 0
    for index, comment in enumerate(comments, start=1):
        user = comment.get("user") or {}
        login = user.get("login", "unknown")
        created_at = comment.get("created_at", "")
        comment_body = (comment.get("body") or "").strip()
        print(f"[{index}] {login} {created_at}".strip())
        if comment_body:
            print(comment_body)
        else:
            print("(empty)")
        if index != len(comments):
            print("")
    return 0

def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    handlers = {
        "status": _run_status,
        "checkout": _run_checkout,
        "branch-delete": _run_branch_delete,
        "pull": _run_pull,
        "push": _run_push,
        "commit": _run_commit,
        "pr-create": _run_pr_create,
        "issues-list": _run_issues_list,
        "issue-pick": _run_issue_pick,
        "issue-details": _run_issue_details,
    }
    if not args.command:
        parser.print_help()
        return 0
    try:
        return handlers[args.command](args)
    except Exception as exc:
        parser.exit(status=1, message=f"Error: {exc}\n")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
