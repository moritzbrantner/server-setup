#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


SANDBOX_REPOS = ("simple-site", "rest-api", "complex-site")


def copy_template(source_repo: Path, target_repo: Path) -> None:
    target_repo.mkdir(parents=True, exist_ok=True)
    for item in source_repo.iterdir():
        if item.name == ".git":
            continue
        destination = target_repo / item.name
        if item.is_dir():
            shutil.copytree(item, destination)
        else:
            shutil.copy2(item, destination)


def initialize_git_repo(target_repo: Path) -> None:
    subprocess.run(["git", "-C", str(target_repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(target_repo), "config", "user.email", "sandbox@example.local"], check=True)
    subprocess.run(["git", "-C", str(target_repo), "config", "user.name", "server-setup sandbox"], check=True)
    subprocess.run(["git", "-C", str(target_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(target_repo), "commit", "-qm", "Initial sandbox example"], check=True)
    subprocess.run(["git", "-C", str(target_repo), "branch", "-M", "main"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed sandbox example repositories.")
    parser.add_argument("--source-dir", default="/opt/server-setup/examples/repositories")
    parser.add_argument("--target-dir", default="/srv/apps")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    target_dir = Path(args.target_dir)
    if not source_dir.is_dir():
        raise SystemExit(f"Source directory not found: {source_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    created = replaced = skipped = 0
    for repo_name in SANDBOX_REPOS:
        source_repo = source_dir / repo_name
        target_repo = target_dir / repo_name
        if not source_repo.is_dir():
            raise SystemExit(f"Example repository template not found: {source_repo}")
        if target_repo.exists():
            if args.force:
                shutil.rmtree(target_repo)
                copy_template(source_repo, target_repo)
                initialize_git_repo(target_repo)
                print(f"Replaced example repository: {repo_name}")
                replaced += 1
            else:
                print(f"Skipped existing example repository: {repo_name}")
                skipped += 1
            continue
        copy_template(source_repo, target_repo)
        initialize_git_repo(target_repo)
        print(f"Created example repository: {repo_name}")
        created += 1

    print(f"Example repository seed summary: created={created} replaced={replaced} skipped={skipped} target_dir={target_dir}")


if __name__ == "__main__":
    main()
