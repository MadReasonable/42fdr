import importlib.util
import io
import tempfile
import textwrap
import types
from typing import Optional
import unittest
from contextlib import redirect_stderr
from pathlib import Path


def _load_42fdr_module() -> types.ModuleType:
    module_path = Path(__file__).with_name("42fdr.py")
    spec = importlib.util.spec_from_file_location("f42_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_42fdr = _load_42fdr_module()


def _make_cli_args(
    config_path: str,
    offset_orig: Optional[str] = None,
    offset_dest: Optional[str] = None,
    airfield_db: Optional[str] = None,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        aircraft=None,
        config=config_path,
        timezone=None,
        outputFolder=None,
        offsetOrig=offset_orig,
        offsetDest=offset_dest,
        airfieldDB=airfield_db,
    )


def _write_temp_config(contents: str) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".conf") as handle:
        handle.write(textwrap.dedent(contents).strip() + "\n")
        return handle.name


class WaypointConfigParsingTests(unittest.TestCase):
    def test_loads_valid_waypoints_with_defaults_and_explicit_radii(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KAAA]
            lat = 40.0
            lon = -75.0
            offset = 10,20,30

            [Waypoint KBBB]
            lat = 41.0
            lon = -76.0
            offset = 1,2,3
            innerRadiusNm = 1.5
            outerRadiusNm = 5.5
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))

        self.assertEqual(2, len(config.configuredWaypoints))
        by_code = {entry.code: entry for entry in config.configuredWaypoints}

        self.assertAlmostEqual(2.0, by_code["KAAA"].innerRadiusNm)
        self.assertAlmostEqual(8.0, by_code["KAAA"].outerRadiusNm)
        self.assertAlmostEqual(10.0, by_code["KAAA"].offset.eastFt)
        self.assertAlmostEqual(20.0, by_code["KAAA"].offset.northFt)
        self.assertAlmostEqual(30.0, by_code["KAAA"].offset.upFt)

        self.assertAlmostEqual(1.5, by_code["KBBB"].innerRadiusNm)
        self.assertAlmostEqual(5.5, by_code["KBBB"].outerRadiusNm)
        self.assertAlmostEqual(1.0, by_code["KBBB"].offset.eastFt)
        self.assertAlmostEqual(2.0, by_code["KBBB"].offset.northFt)
        self.assertAlmostEqual(3.0, by_code["KBBB"].offset.upFt)

    def test_skips_invalid_waypoints_and_emits_warnings(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint MISSING_COORDS]
            offset = 1,2,3

            [Waypoint BAD_OFFSET]
            lat = 42.0
            lon = -70.0
            offset = not-a-valid-offset

            [Waypoint BAD_RADIUS]
            lat = 43.0
            lon = -71.0
            offset = 4,5,6
            innerRadiusNm = nope
            """
        )

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(_make_cli_args(cfg_path))

        output = stderr.getvalue()
        self.assertEqual(1, len(config.configuredWaypoints))
        self.assertTrue(config.configuredWaypoints[0].hasCoordinates())
        self.assertIn("Skipping [Waypoint MISSING_COORDS] because both lat and lon are required in phase 1.", output)
        self.assertIn("Skipping [Waypoint BAD_OFFSET] because offset is invalid:", output)
        self.assertIn("Ignoring invalid innerradiusnm value in [Waypoint BAD_RADIUS]", output)

    def test_missing_lat_lon_allowed_when_airfield_lookup_enabled(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KABC]
            offset = 1,2,3
            """
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        self.assertEqual(1, len(config.configuredWaypoints))
        self.assertFalse(config.configuredWaypoints[0].hasCoordinates())
        self.assertEqual("", stderr.getvalue())


class AirfieldDbConfigTests(unittest.TestCase):
    def test_airfielddb_no_value_uses_script_directory_default_filename(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        self.assertTrue(config.airfieldDbEnabled)
        self.assertEqual("OurAirports.csv", config.airfieldDbPath.name)
        self.assertEqual(Path(_42fdr.__file__).resolve().parent, config.airfieldDbPath.resolve().parent)

    def test_airfielddb_max_age_default_and_override(self) -> None:
        default_cfg = _write_temp_config(
            """
            [Defaults]
            """
        )
        overridden_cfg = _write_temp_config(
            """
            [Defaults]
            airfieldDBMaxAgeDays = 30
            """
        )
        default_config = _42fdr.Config(_make_cli_args(default_cfg))
        overridden_config = _42fdr.Config(_make_cli_args(overridden_cfg))
        self.assertAlmostEqual(90.0, default_config.airfieldDbMaxAgeDays)
        self.assertAlmostEqual(30.0, overridden_config.airfieldDbMaxAgeDays)


class AirportOffsetBlendingTests(unittest.TestCase):
    def test_no_match_returns_none(self) -> None:
        helper = _42fdr.AirportOffsetHelper()
        helper.addAirport("KAAA", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)

        self.assertIsNone(helper._offsetFeetForPosition(1.0, 0.0))
        self.assertIsNone(helper.offsetForPosition(1.0, 0.0))

    def test_inner_radius_blending_uses_inverse_ratio_weights(self) -> None:
        helper = _42fdr.AirportOffsetHelper()
        helper.addAirport("A", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)
        helper.addAirport("B", 0.0, 1.0 / 60.0, _42fdr.CardinalOffset(20.0, 0.0, 0.0), 2.0, 4.0)

        # Near A (0.25 NM from A, 0.75 NM from B) => A should dominate 3:1 by inverse ratio.
        blended = helper._offsetFeetForPosition(0.0, 0.25 / 60.0)
        self.assertIsNotNone(blended)
        self.assertAlmostEqual(12.5, blended.eastFt, places=1)

    def test_outer_ring_tapers_with_distance(self) -> None:
        helper = _42fdr.AirportOffsetHelper()
        helper.addAirport("KAAA", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)

        # At 3 NM in a 2-4 NM ring => local weight (4-3)/(4-2) = 0.5 => east offset 5 ft.
        blended = helper._offsetFeetForPosition(0.0, 3.0 / 60.0)
        self.assertIsNotNone(blended)
        self.assertAlmostEqual(5.0, blended.eastFt, places=1)

    def test_overlapping_outer_entries_blend_deterministically(self) -> None:
        helper = _42fdr.AirportOffsetHelper()
        helper.addAirport("A", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)
        helper.addAirport("B", 0.0, 0.0, _42fdr.CardinalOffset(30.0, 0.0, 0.0), 2.0, 4.0)

        # Same center and ring weight for both => equal blend of local offsets.
        blended = helper._offsetFeetForPosition(0.0, 3.0 / 60.0)
        self.assertIsNotNone(blended)
        self.assertAlmostEqual(10.0, blended.eastFt, places=1)


class MergePathTests(unittest.TestCase):
    def test_airport_offsets_for_flight_merges_waypoints_and_cli_offsets(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KCFG]
            lat = 40.05
            lon = -73.05
            offset = 4,5,6
            """
        )
        config = _42fdr.Config(
            _make_cli_args(
                config_path=cfg_path,
                offset_orig="1,2,3",
                offset_dest="7,8,9",
            )
        )

        flight_meta = _42fdr.FlightMeta()
        flight_meta.DerivedOrigin = "KORIG"
        flight_meta.DerivedDestination = "KDEST"
        track_data = [
            {"Latitude": 40.0, "Longitude": -73.0},
            {"Latitude": 41.0, "Longitude": -72.0},
        ]

        flight = _42fdr.FdrFlight()
        flight.metaData = flight_meta
        flight.trackData = track_data

        boundingBoxes = flight.buildBoundingBoxes(config.airfieldGridCellNm)
        helper = config.offsetHelperForFlight(flight, boundingBoxes)
        self.assertEqual(3, len(helper._entries))

        by_code = {entry.code: entry for entry in helper._entries}
        self.assertIn("KCFG", by_code)
        self.assertIn("KORIG", by_code)
        self.assertIn("KDEST", by_code)
        self.assertAlmostEqual(1.0, by_code["KORIG"].offset.eastFt)
        self.assertAlmostEqual(8.0, by_code["KDEST"].offset.northFt)
        self.assertAlmostEqual(4.0, by_code["KCFG"].offset.eastFt)

    def test_waypoint_prefilter_skips_far_configured_waypoints(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KNEAR]
            lat = 40.02
            lon = -73.02
            offset = 1,1,1

            [Waypoint KFAR]
            lat = 10.0
            lon = -140.0
            offset = 2,2,2
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        flight = _42fdr.FdrFlight()
        flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}, {"Latitude": 40.2, "Longitude": -72.8}]
        boundingBoxes = flight.buildBoundingBoxes(config.airfieldGridCellNm)
        helper = config.offsetHelperForFlight(flight, boundingBoxes)
        by_code = {entry.code: entry for entry in helper._entries}
        self.assertIn("KNEAR", by_code)
        self.assertNotIn("KFAR", by_code)


class FlightBoundingBoxTests(unittest.TestCase):
    def test_build_bounding_boxes_partitions_long_tracks(self) -> None:
        flight = _42fdr.FdrFlight()
        # About 420 NM east-west with a 100 NM grid should create multiple boxes.
        flight.trackData = [
            {"Latitude": 40.0, "Longitude": -80.0},
            {"Latitude": 40.0, "Longitude": -74.0},
            {"Latitude": 40.0, "Longitude": -68.0},
        ]
        boundingBoxes = flight.buildBoundingBoxes(100.0)
        self.assertGreaterEqual(len(boundingBoxes), 3)


if __name__ == "__main__":
    unittest.main()
