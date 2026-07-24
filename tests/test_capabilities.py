from __future__ import annotations

import unittest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.models import KernelMode


class CapabilityRegistryTests(unittest.TestCase):
    def test_exact_alias_wins(self):
        registry = CapabilityRegistry()
        result = registry.search("db exec")
        self.assertEqual(result[0].tool_name, "data.sql.exec.v1")

    def test_kernel_filter_hides_other_surface(self):
        registry = CapabilityRegistry()
        result = registry.search("service", kernel=KernelMode.DATA)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
