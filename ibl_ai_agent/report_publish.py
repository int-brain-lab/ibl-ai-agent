from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
import os
import re
import shutil
import socket
import subprocess


DEFAULT_REPORTS_REPO = "ibl-ai-agent-reports"
DEFAULT_PUBLISH_ROOT = Path(".ibl-ai-agent-publish")

WEB_EXTENSIONS = {
    ".css",
    ".gif",
    ".htm",
    ".html",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".otf",
    ".png",
    ".svg",
    ".ttf",
    ".txt",
    ".webp",
    ".woff",
    ".woff2",
}

RISKY_EXTENSIONS = {
    ".csv",
    ".db",
    ".env",
    ".feather",
    ".h5",
    ".hdf5",
    ".ipynb",
    ".jsonl",
    ".log",
    ".npy",
    ".npz",
    ".parquet",
    ".pkl",
    ".pickle",
    ".sqlite",
    ".sqlite3",
    ".tsv",
    ".yaml",
    ".yml",
}

RISKY_FILENAMES = {
    ".env",
    ".env.private",
    "alyx.json",
    "credentials",
    "credentials.json",
    "data_locations.local.yaml",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "known_hosts",
}

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    # A quoted value (also \"escaped\") runs to its closing quote, else to the next space.
    re.compile(
        r"(?i)(password|passwd|token|api[_-]?key|secret)\\?['\"]?\s*[:=]\s*"
        r"(?:(\\?['\"])(?:(?!(?<!\\)\2).){8,}(?<!\\)\2|\\?['\"]?[^'\"\s<]{8,})"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    # The whole key; to the end of the text when the END line is cut off.
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
        r"(?:.*?-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|.*)",
        re.S,
    ),
]

WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:Users|Neuropixels Dropbox|Documents and Settings)\\[^<>'\"\s]+")
POSIX_HOME_RE = re.compile(r"/(?:Users|home)/[^<>'\"\s]+")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class PublishFinding:
    severity: str
    path: str
    message: str


@dataclass(frozen=True)
class StagedReport:
    source: Path
    site_dir: Path
    report_dir: Path
    index_path: Path
    slug: str
    title: str
    files: tuple[str, ...]
    findings: tuple[PublishFinding, ...]

    @property
    def has_blockers(self) -> bool:
        return any(f.severity == "blocker" for f in self.findings)


@dataclass(frozen=True)
class GithubPublishResult:
    url: str
    repo_full_name: str
    local_repo: Path
    staged: StagedReport


class PublishError(RuntimeError):
    """Raised for publish staging or GitHub workflow failures."""


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "report"


def default_report_slug(report_path: Path) -> str:
    path = report_path.expanduser()
    if path.is_file():
        if path.name.lower() in {"index.html", "report.html"} and path.parent.name:
            return slugify(path.parent.parent.name if path.parent.name == "report" else path.parent.name)
        return slugify(path.stem)
    if path.name.lower() == "report" and path.parent.name:
        return slugify(path.parent.name)
    return slugify(path.name)


def stage_report_site(
    report_path: Path,
    *,
    site_dir: Path,
    slug: str | None = None,
    title: str | None = None,
    overwrite: bool = False,
    allow_risky: bool = False,
) -> StagedReport:
    source = report_path.expanduser().resolve()
    entrypoint, source_root = _resolve_report_entrypoint(source)
    report_slug = slugify(slug or default_report_slug(source))
    report_title = title or report_slug.replace("-", " ").title()
    site_root = site_dir.expanduser()
    destination = site_root / report_slug

    if destination.exists():
        if not overwrite:
            raise PublishError(
                f"Published report already exists at {destination}; pass --overwrite to replace it."
            )
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    findings: list[PublishFinding] = []
    for path in sorted(p for p in source_root.rglob("*") if p.is_file()):
        rel = path.relative_to(source_root)
        risk = _risky_file_reason(path)
        if risk:
            findings.append(PublishFinding("blocker", str(rel), risk))
            continue
        if path.suffix.lower() not in WEB_EXTENSIONS:
            findings.append(
                PublishFinding("warning", str(rel), "non-web file type skipped")
            )
            continue
        target_rel = Path("index.html") if path == entrypoint else rel
        target = destination / target_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(str(Path(report_slug) / target_rel).replace("\\", "/"))

    if not (destination / "index.html").exists():
        raise PublishError("Report staging failed: no index.html was produced.")

    staged_findings = findings + scan_publish_tree(destination)
    if any(f.severity == "blocker" for f in staged_findings) and not allow_risky:
        _write_root_index(site_root)
        return StagedReport(
            source=source,
            site_dir=site_root,
            report_dir=destination,
            index_path=site_root / "index.html",
            slug=report_slug,
            title=report_title,
            files=tuple(sorted(copied)),
            findings=tuple(staged_findings),
        )

    _write_root_index(site_root)
    (site_root / ".nojekyll").write_text("", encoding="utf-8")
    return StagedReport(
        source=source,
        site_dir=site_root,
        report_dir=destination,
        index_path=site_root / "index.html",
        slug=report_slug,
        title=report_title,
        files=tuple(sorted(copied)),
        findings=tuple(staged_findings),
    )


def scan_publish_tree(root: Path) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    home = str(Path.home())
    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    hostname = socket.gethostname()

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root)).replace("\\", "/")
        risk = _risky_file_reason(path)
        if risk:
            findings.append(PublishFinding("blocker", rel, risk))

        if path.suffix.lower() in {".html", ".htm", ".css", ".js", ".json", ".txt", ".svg"}:
            text = _read_text_sample(path)
            if text is None:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(PublishFinding("blocker", rel, "credential-like text detected"))
                    break
            if WINDOWS_PATH_RE.search(text) or POSIX_HOME_RE.search(text) or (home and home in text):
                findings.append(PublishFinding("blocker", rel, "absolute local path detected"))
            if username and len(username) >= 3 and re.search(rf"(?i)\b{re.escape(username)}\b", text):
                findings.append(PublishFinding("warning", rel, "local username appears in text"))
            if hostname and len(hostname) >= 3 and re.search(rf"(?i)\b{re.escape(hostname)}\b", text):
                findings.append(PublishFinding("warning", rel, "computer name appears in text"))
    return findings


def publish_report_to_github(
    report_path: Path,
    *,
    owner: str,
    repo: str = DEFAULT_REPORTS_REPO,
    slug: str | None = None,
    title: str | None = None,
    publish_root: Path = DEFAULT_PUBLISH_ROOT,
    overwrite: bool = False,
    confirm_public: bool = False,
    allow_risky: bool = False,
    dry_run: bool = False,
) -> GithubPublishResult:
    if not owner:
        raise PublishError("--owner is required.")
    if not confirm_public and not dry_run:
        raise PublishError("Refusing to publish without --confirm-public.")

    local_repo = publish_root.expanduser() / f"{owner}-{repo}"
    site_dir = local_repo / "docs"
    if not dry_run:
        _ensure_gh_ready()
        _ensure_repo(owner=owner, repo=repo)
        _ensure_local_repo(owner=owner, repo=repo, local_repo=local_repo)
    else:
        local_repo.mkdir(parents=True, exist_ok=True)

    staged = stage_report_site(
        report_path,
        site_dir=site_dir,
        slug=slug,
        title=title,
        overwrite=overwrite,
        allow_risky=allow_risky,
    )
    if staged.has_blockers and not allow_risky:
        raise PublishError(_format_blockers(staged.findings))

    url = f"https://{owner}.github.io/{repo}/{staged.slug}/"
    if dry_run:
        return GithubPublishResult(
            url=url,
            repo_full_name=f"{owner}/{repo}",
            local_repo=local_repo,
            staged=staged,
        )

    _git(local_repo, "checkout", "-B", "main")
    _git(local_repo, "add", "docs")
    status = _git(local_repo, "status", "--short", capture=True).stdout.strip()
    if status:
        _git(local_repo, "commit", "-m", f"Publish {staged.slug} report")
        _git(local_repo, "push", "-u", "origin", "main")
    _enable_pages(owner=owner, repo=repo)
    return GithubPublishResult(
        url=url,
        repo_full_name=f"{owner}/{repo}",
        local_repo=local_repo,
        staged=staged,
    )


def _resolve_report_entrypoint(path: Path) -> tuple[Path, Path]:
    if not path.exists():
        raise PublishError(f"Report path does not exist: {path}")
    if path.is_file():
        if path.suffix.lower() not in {".html", ".htm"}:
            raise PublishError(f"Report file must be HTML: {path}")
        return path, path.parent
    candidates = [path / "index.html", path / "report.html"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate, path
    raise PublishError(f"Report directory must contain index.html or report.html: {path}")


def _risky_file_reason(path: Path) -> str | None:
    name = path.name.lower()
    if name in RISKY_FILENAMES:
        return "risky filename is not safe for public upload"
    if path.suffix.lower() in RISKY_EXTENSIONS:
        return "risky file type is not safe for public upload"
    return None


def _read_text_sample(path: Path, *, max_bytes: int = 50_000_000) -> str | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _write_root_index(site_dir: Path) -> Path:
    report_dirs = sorted(p for p in site_dir.iterdir() if p.is_dir() and (p / "index.html").is_file())
    links = "\n".join(
        f'        <li><a href="{escape(path.name)}/">{escape(path.name.replace("-", " ").title())}</a></li>'
        for path in report_dirs
    )
    if not links:
        links = "        <li>No reports have been published yet.</li>"
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>IBL AI Agent Reports</title>
  </head>
  <body>
    <h1>IBL AI Agent Reports</h1>
    <ul>
{links}
    </ul>
  </body>
</html>
"""
    site_dir.mkdir(parents=True, exist_ok=True)
    index_path = site_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    return index_path


def _format_blockers(findings: tuple[PublishFinding, ...]) -> str:
    blockers = [f for f in findings if f.severity == "blocker"]
    lines = ["Privacy/security preflight blocked publication:"]
    for finding in blockers[:20]:
        lines.append(f"- {finding.path}: {finding.message}")
    if len(blockers) > 20:
        lines.append(f"- ... {len(blockers) - 20} more blockers")
    lines.append("Review the report and re-run only when it is safe to publish.")
    return "\n".join(lines)


def _ensure_gh_ready() -> None:
    if shutil.which("gh") is None:
        raise PublishError(
            "GitHub CLI 'gh' is not installed. Install it from https://cli.github.com/ "
            "and run 'gh auth login' before publishing."
        )
    _run(["gh", "auth", "status"], check_error="GitHub CLI is not authenticated. Run 'gh auth login'.")


def _ensure_repo(*, owner: str, repo: str) -> None:
    full_name = f"{owner}/{repo}"
    proc = _run(["gh", "repo", "view", full_name], check=False)
    if proc.returncode == 0:
        return
    _run(["gh", "repo", "create", full_name, "--public", "--add-readme"])


def _ensure_local_repo(*, owner: str, repo: str, local_repo: Path) -> None:
    full_name = f"{owner}/{repo}"
    if (local_repo / ".git").is_dir():
        _git(local_repo, "pull", "--ff-only")
        return
    local_repo.parent.mkdir(parents=True, exist_ok=True)
    _run(["gh", "repo", "clone", full_name, str(local_repo)])
    if not (local_repo / "docs").exists():
        (local_repo / "docs").mkdir(parents=True, exist_ok=True)


def _enable_pages(*, owner: str, repo: str) -> None:
    full_name = f"{owner}/{repo}"
    proc = _run(["gh", "api", f"repos/{full_name}/pages"], check=False)
    if proc.returncode == 0:
        return
    _run(
        [
            "gh",
            "api",
            f"repos/{full_name}/pages",
            "--method",
            "POST",
            "-f",
            "source[branch]=main",
            "-f",
            "source[path]=/docs",
        ]
    )


def _git(cwd: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], cwd=cwd, capture=capture)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
    check_error: str | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and proc.returncode != 0:
        message = check_error or (proc.stderr.strip() if proc.stderr else f"Command failed: {' '.join(args)}")
        raise PublishError(message)
    return proc
