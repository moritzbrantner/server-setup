import unittest
from server_setup.config import ConfigError, parse_config

class Pr2ConfigTests(unittest.TestCase):
    def test_dokploy_version_must_be_exactly_pinned(self) -> None:
        with self.assertRaisesRegex(ConfigError, "exact pinned release"): parse_config('version = 1\n[dokploy]\nversion = "latest"\n')
        with self.assertRaisesRegex(ConfigError, "exact pinned release"): parse_config('version = 1\n[dokploy]\nversion = "canary"\n')
        self.assertEqual(parse_config('version = 1\n[dokploy]\nversion = "v0.30.2"\n').dokploy.version, "v0.30.2")

if __name__ == "__main__": unittest.main()
