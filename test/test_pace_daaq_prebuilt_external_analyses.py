#!/usr/bin/env python3

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class TestPaceDaaqPrebuiltExternalAnalyses(unittest.TestCase):
    def test_shared_2024_config_uses_link_only_gfs_initial_conditions(self):
        shared = yaml.safe_load(
            (ROOT / "scenarios/PACE-DAAQ_GOCART2G_shared.yaml").read_text()
        )["externalanalyses"]
        defaults = yaml.safe_load(
            (ROOT / "scenarios/defaults/externalanalyses.yaml").read_text()
        )["externalanalyses"]["resources"]

        self.assertEqual(shared["resource"], "GFS.PANDAC")
        directory = shared["resources"]["GFS"]["PANDAC"]["60km"]["directory"]
        self.assertEqual(
            directory,
            "/glade/derecho/scratch/syha/ExternalAnalyses_shared/60km",
        )

        # GFS.PANDAC intentionally inherits the global pre-staged task chain.
        # This guards against accidentally restoring RDA download/ungrib/init.
        tasks = defaults["defaults"]["PrepareExternalAnalysisTasks"]
        self.assertEqual(
            tasks,
            ["LinkExternalAnalysis-{{mesh}}", "ExternalAnalysisReady__"],
        )
        self.assertNotIn("UngribExternalAnalysis", tasks)
        self.assertFalse(any("ExternalAnalysisToMPAS" in task for task in tasks))


if __name__ == "__main__":
    unittest.main()
