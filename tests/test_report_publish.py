from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from typer.testing import CliRunner

from ibl_ai_agent.cli import app
from ibl_ai_agent import report_publish, secret_scan
from ibl_ai_agent.report_publish import PublishError, publish_report_to_github, stage_report_site


def _write_report(root: Path, *, entry_name: str = "report.html") -> Path:
    report_dir = root / "projects" / "latency_project" / "report"
    asset_dir = report_dir / "report_files"
    asset_dir.mkdir(parents=True)
    (report_dir / entry_name).write_text(
        '<html><head><link rel="stylesheet" href="report_files/style.css"></head>'
        '<body><img src="report_files/plot.png"></body></html>',
        encoding="utf-8",
    )
    (asset_dir / "style.css").write_text("body { color: #111; }", encoding="utf-8")
    (asset_dir / "plot.png").write_bytes(b"png")
    return report_dir


def test_stage_report_site_from_report_html(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    staged = stage_report_site(report_dir, site_dir=tmp_path / "site")

    assert staged.slug == "latency-project"
    assert (tmp_path / "site" / "latency-project" / "index.html").exists()
    assert (tmp_path / "site" / "latency-project" / "report_files" / "style.css").exists()
    assert (tmp_path / "site" / "index.html").read_text(encoding="utf-8").count("latency-project/") == 1
    assert not staged.has_blockers


def test_stage_report_site_from_index_html(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path, entry_name="index.html")
    staged = stage_report_site(report_dir, site_dir=tmp_path / "site", slug="custom-slug")

    assert staged.slug == "custom-slug"
    assert (tmp_path / "site" / "custom-slug" / "index.html").exists()


def test_stage_report_site_refuses_existing_slug_without_overwrite(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    stage_report_site(report_dir, site_dir=tmp_path / "site", slug="latency")

    with pytest.raises(PublishError, match="already exists"):
        stage_report_site(report_dir, site_dir=tmp_path / "site", slug="latency")


def test_stage_report_site_detects_risky_files_and_paths(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    (report_dir / "secrets.yaml").write_text("password: abcdefghijk", encoding="utf-8")
    (report_dir / "report.html").write_text(
        r"<html><body>C:\Users\Kenneth\private\file.txt"
        "<pre>-----BEGIN RSA PRIVATE KEY-----</pre></body></html>",
        encoding="utf-8",
    )

    staged = stage_report_site(report_dir, site_dir=tmp_path / "site")

    messages = [finding.message for finding in staged.findings]
    assert staged.has_blockers
    assert "risky file type is not safe for public upload" in messages
    assert "absolute local path detected" in messages
    assert "credential-like text detected" in messages


@pytest.mark.parametrize(
    "body",
    [
        "<pre>{&quot;password&quot;: &quot;correct horse battery staple&quot;}</pre>",
        '<span class="va">password</span> <span class="op">=</span> '
        '<span class="st">&quot;correct horse battery staple&quot;</span>',
    ],
)
def test_stage_report_site_detects_secrets_in_rendered_code(tmp_path: Path, body: str) -> None:
    # Quarto writes quotes as &quot; and wraps highlighted code in <span>s.
    report_dir = _write_report(tmp_path)
    (report_dir / "report.html").write_text(f"<html><body>{body}</body></html>", encoding="utf-8")

    staged = stage_report_site(report_dir, site_dir=tmp_path / "site")

    assert "credential-like text detected" in [finding.message for finding in staged.findings]


def test_scan_publish_tree_blocks_files_it_cannot_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(secret_scan, "MAX_SCAN_BYTES", 5)
    root = tmp_path / "report"
    root.mkdir()
    (root / "big.txt").write_text("more than five bytes", encoding="utf-8")
    (tmp_path / "private.html").write_text("<p>hi</p>", encoding="utf-8")
    (root / "link.html").symlink_to(tmp_path / "private.html")

    findings = {(f.path, f.message) for f in secret_scan.scan_publish_tree(root)}

    assert ("big.txt", "could not be read to check for secrets") in findings
    assert ("link.html", "links to a file outside the folder") in findings


def test_stage_report_site_skips_non_web_auxiliary_files(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    (report_dir / "report.qmd").write_text("# Source document", encoding="utf-8")
    (report_dir / "report.pdf").write_bytes(b"%PDF-1.7")
    (report_dir / "instruction_suggestions.md").write_text("Notes", encoding="utf-8")

    staged = stage_report_site(report_dir, site_dir=tmp_path / "site")

    assert not staged.has_blockers
    assert sorted(finding.path for finding in staged.findings) == [
        "instruction_suggestions.md",
        "report.pdf",
        "report.qmd",
    ]
    assert {finding.message for finding in staged.findings} == {"non-web file type skipped"}
    assert staged.files == (
        "latency-project/index.html",
        "latency-project/report_files/plot.png",
        "latency-project/report_files/style.css",
    )


def test_publish_report_to_github_dry_run_url(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    result = publish_report_to_github(
        report_dir,
        owner="test-owner",
        publish_root=tmp_path / "publish",
        dry_run=True,
    )

    assert result.url == "https://test-owner.github.io/ibl-ai-agent-reports/latency-project/"
    assert result.repo_full_name == "test-owner/ibl-ai-agent-reports"


def test_publish_report_to_github_requires_confirmation(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)

    with pytest.raises(PublishError, match="confirm-public"):
        publish_report_to_github(report_dir, owner="test-owner", publish_root=tmp_path / "publish")


def test_publish_report_to_github_missing_gh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    monkeypatch.setattr(report_publish.shutil, "which", lambda name: None)

    with pytest.raises(PublishError, match="GitHub CLI 'gh' is not installed"):
        publish_report_to_github(
            report_dir,
            owner="test-owner",
            publish_root=tmp_path / "publish",
            confirm_public=True,
        )


def test_publish_report_to_github_success_with_mocked_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report_dir = _write_report(tmp_path)
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(report_publish.shutil, "which", lambda name: "gh")
    monkeypatch.setattr(report_publish, "_ensure_repo", lambda owner, repo: calls.append(("repo", owner, repo)))
    monkeypatch.setattr(
        report_publish,
        "_enable_pages",
        lambda owner, repo: calls.append(("pages", owner, repo)),
    )

    def fake_ensure_local_repo(*, owner: str, repo: str, local_repo: Path) -> None:
        calls.append(("clone", owner, repo))
        (local_repo / ".git").mkdir(parents=True)
        (local_repo / "docs").mkdir()

    def fake_run(
        args: list[str],
        *,
        cwd: Path | None = None,
        capture: bool = True,
        check: bool = True,
        check_error: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(args))
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:3] == ["git", "status", "--short"]:
            return subprocess.CompletedProcess(args, 0, " M docs/index.html\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(report_publish, "_ensure_local_repo", fake_ensure_local_repo)
    monkeypatch.setattr(report_publish, "_run", fake_run)

    result = publish_report_to_github(
        report_dir,
        owner="test-owner",
        publish_root=tmp_path / "publish",
        confirm_public=True,
    )

    assert result.url == "https://test-owner.github.io/ibl-ai-agent-reports/latency-project/"
    assert ("repo", "test-owner", "ibl-ai-agent-reports") in calls
    assert ("pages", "test-owner", "ibl-ai-agent-reports") in calls
    assert ("git", "checkout", "-B", "main") in calls
    assert ("git", "commit", "-m", "Publish latency-project report") in calls
    assert ("git", "push", "-u", "origin", "main") in calls


def test_stage_report_site_cli(tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "stage-report-site",
            str(report_dir),
            "--site-dir",
            str(tmp_path / "site"),
            "--slug",
            "latency",
        ],
    )

    assert result.exit_code == 0
    assert "This report will be public." in result.stdout
    assert "How to check before confirming:" in result.stdout
    assert "preflight_findings: none" in result.stdout
    assert (tmp_path / "site" / "latency" / "index.html").exists()


def test_publish_cli_prompts_after_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    report_dir = _write_report(tmp_path)
    calls: list[bool] = []

    def fake_publish(*args, **kwargs):
        calls.append(bool(kwargs["dry_run"]))
        return publish_report_to_github(*args, **kwargs)

    monkeypatch.setattr("ibl_ai_agent.commands.report_commands.publish_report_to_github", fake_publish)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "publish-report-to-github",
            str(report_dir),
            "--owner",
            "test-owner",
            "--publish-root",
            str(tmp_path / "publish"),
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert calls == [True]
    assert "files_to_publish:" in result.stdout
    assert "Publish these files publicly on GitHub Pages?" in result.stdout
    assert "Publication cancelled." in result.output
