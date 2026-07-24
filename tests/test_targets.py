from __future__ import annotations

import unittest

from lighthouse.models import TargetKind
from lighthouse.targets import validate_target_config


class TargetValidationTests(unittest.TestCase):
    def test_data_target_accepts_only_secret_reference(self):
        value = validate_target_config(TargetKind.DATA, {"dsn_env": "WAREHOUSE_DATABASE_URL"})
        self.assertEqual(value["dsn_env"], "WAREHOUSE_DATABASE_URL")
        with self.assertRaises(ValueError):
            validate_target_config(TargetKind.DATA, {"dsn": "postgresql://secret"})

    def test_ssh_target_requires_host_and_user(self):
        with self.assertRaises(ValueError):
            validate_target_config(TargetKind.SYSTEM, {"transport": "ssh"})
        value = validate_target_config(TargetKind.SYSTEM, {"transport": "ssh", "host": "server.example.com", "user": "warehouse", "identity_file_env": "WAREHOUSE_SSH_KEY"})
        self.assertEqual(value["port"], 22)


if __name__ == "__main__":
    unittest.main()
