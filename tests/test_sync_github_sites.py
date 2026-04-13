#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import unittest


def load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "sync_github_sites.py"
    spec = importlib.util.spec_from_file_location("sync_github_sites", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SyncGithubSitesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_module()

    def test_resolve_site_fields_supports_top_level_shorthand(self) -> None:
        site = self.module.resolve_site_fields(
            {
                "name": "marketing-site",
                "repo": "https://github.com/your-org/marketing-site.git",
                "domain": "example.com",
                "build_output": "dist",
                "build": "npm ci && npm run build",
                "command": "PORT=4003 npm run start",
                "port": 4003,
                "user": "www-data",
                "env_file": "/etc/default/marketing-site",
                "health_endpoint": "/healthz",
                "reload_cmd": "sudo systemctl reload nginx",
                "www_redirect": True,
                "tls_hostnames": ["example.com", "www.example.com"],
            }
        )

        self.assertEqual(site["service_name"], "marketing-site.service")
        self.assertEqual(site["workdir"], "/srv/github-sites/marketing-site")
        self.assertEqual(site["site_url"], "")
        self.assertEqual(site["build_cmd"], "npm ci && npm run build")
        self.assertEqual(site["runtime_mode"], "service")
        self.assertEqual(site["runtime_command"], "PORT=4003 npm run start")
        self.assertEqual(site["runtime_port"], "4003")
        self.assertEqual(site["runtime_user"], "www-data")
        self.assertEqual(site["runtime_env_file"], "/etc/default/marketing-site")
        self.assertEqual(site["runtime_health_endpoint"], "/healthz")
        self.assertEqual(site["post_deploy_cmd"], "sudo systemctl reload nginx")
        self.assertTrue(site["nginx_www_redirect"])
        self.assertEqual(site["nginx_tls_hostnames_csv"], "example.com www.example.com")

    def test_resolve_site_fields_builds_github_token_url_from_https_repo(self) -> None:
        site = self.module.resolve_site_fields(
            {
                "name": "tlm-deutschland",
                "repo": "https://github.com/moritzbrantner/tlm-deutschland.git",
                "workdir": "/srv/apps/tlm-deutschland",
                "build_output": ".",
                "runtime": {"mode": "service", "command": "bun run start", "port": 3001},
                "repo_auth": {
                    "github_token": "secret-token",
                    "github_username": "x-access-token",
                },
            }
        )

        self.assertEqual(
            site["repo"],
            "https://x-access-token:secret-token@github.com/moritzbrantner/tlm-deutschland.git",
        )

    def test_resolve_site_fields_builds_github_token_url_from_ssh_repo(self) -> None:
        site = self.module.resolve_site_fields(
            {
                "name": "tlm-deutschland",
                "repo": "git@github.com:moritzbrantner/tlm-deutschland.git",
                "workdir": "/srv/apps/tlm-deutschland",
                "build_output": ".",
                "runtime": {"mode": "service", "command": "bun run start", "port": 3001},
                "repo_auth": {"github_token": "secret-token"},
            }
        )

        self.assertEqual(
            site["repo"],
            "https://x-access-token:secret-token@github.com/moritzbrantner/tlm-deutschland.git",
        )


if __name__ == "__main__":
    unittest.main()
