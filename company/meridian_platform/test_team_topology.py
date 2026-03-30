#!/usr/bin/env python3
import unittest

from team_topology import SPECIALIST_KEYS, load_team_topology


class TeamTopologyTests(unittest.TestCase):
    def test_load_team_topology_reads_meridian_env_defaults(self):
        topology = load_team_topology()
        self.assertEqual(topology.manager.name, "Leviathann")
        self.assertEqual(topology.manager.profile_name, "manager_frontier")
        self.assertEqual(len(topology.specialists), len(SPECIALIST_KEYS))
        specialist_map = {agent.env_key: agent for agent in topology.specialists}
        for key in SPECIALIST_KEYS:
            self.assertIn(key, specialist_map)
            self.assertTrue(specialist_map[key].base_url)
            self.assertTrue(specialist_map[key].model)
            self.assertEqual(specialist_map[key].provider_kind, "openai_compatible")


if __name__ == "__main__":
    unittest.main()
