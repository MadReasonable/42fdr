import importlib.util
import io
import tempfile
import textwrap
import types
from typing import Optional
import unittest
from contextlib import redirect_stderr
from pathlib import Path


def _load_42fdr_module() -> tuple[types.ModuleType, Path]:
    module_path = Path(__file__).with_name("42fdr.py")
    spec = importlib.util.spec_from_file_location("f42_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module_path


_42fdr, _42fdr_path = _load_42fdr_module()


def _make_cli_args(
    config_path: str,
    offset_orig: Optional[str] = None,
    offset_dest: Optional[str] = None,
    airfield_db: Optional[str] = None,
    aircraft_type: Optional[str] = None,
    infer_route: bool = False,
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        aircraft=None,
        aircraftType=aircraft_type,
        config=config_path,
        timezone=None,
        outputFolder=None,
        offsetOrig=offset_orig,
        offsetDest=offset_dest,
        airfieldDB=airfield_db,
        inferRoute=infer_route,
    )


def _write_temp_config(contents: str) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".conf") as handle:
        handle.write(textwrap.dedent(contents).strip() + "\n")
        return handle.name


class CliParsingTests(unittest.TestCase):
    def test_airfielddb_flag_keeps_first_trackfile_positional(self) -> None:
        parser = _42fdr._buildArgParser()
        args = parser.parse_args(["--airfieldDB", "track.csv"])
        self.assertEqual("", args.airfieldDB)
        self.assertEqual(["track.csv"], args.trackfile)

    def test_airfielddb_flag_keeps_multiple_trackfiles_positional(self) -> None:
        parser = _42fdr._buildArgParser()
        args = parser.parse_args(["--airfieldDB", "track1.csv", "track2.kml"])
        self.assertEqual("", args.airfieldDB)
        self.assertEqual(["track1.csv", "track2.kml"], args.trackfile)

    def test_airfielddbpath_sets_explicit_database_path(self) -> None:
        parser = _42fdr._buildArgParser()
        args = parser.parse_args(["--airfieldDBPath", "/tmp/OurAirports.csv", "track.csv"])
        self.assertEqual("/tmp/OurAirports.csv", args.airfieldDB)
        self.assertEqual(["track.csv"], args.trackfile)

    def test_inferroute_flag_sets_true(self) -> None:
        parser = _42fdr._buildArgParser()
        args = parser.parse_args(["--inferRoute", "track.csv"])
        self.assertTrue(args.inferRoute)
        self.assertEqual(["track.csv"], args.trackfile)


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

        self.assertEqual(2, len(config.waypoints))
        by_code = {entry.code: entry for entry in config.waypoints}

        self.assertAlmostEqual(2.0, by_code["KAAA"].innerRadiusNm)
        self.assertAlmostEqual(6.0, by_code["KAAA"].outerRadiusNm)
        self.assertAlmostEqual(10.0, by_code["KAAA"].offset.eastFt)
        self.assertAlmostEqual(20.0, by_code["KAAA"].offset.northFt)
        self.assertAlmostEqual(30.0, by_code["KAAA"].offset.upFt)

        self.assertAlmostEqual(1.5, by_code["KBBB"].innerRadiusNm)
        self.assertAlmostEqual(5.5, by_code["KBBB"].outerRadiusNm)
        self.assertAlmostEqual(1.0, by_code["KBBB"].offset.eastFt)
        self.assertAlmostEqual(2.0, by_code["KBBB"].offset.northFt)
        self.assertAlmostEqual(3.0, by_code["KBBB"].offset.upFt)

    def test_config_error_when_waypoint_missing_coords_without_airfield_db(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint MISSING_COORDS]
            offset = 1,2,3
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("MISSING_COORDS", str(ctx.exception))
        self.assertIn("lat and lon", str(ctx.exception))

    def test_config_error_when_waypoint_offset_invalid(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint BAD_OFFSET]
            lat = 42.0
            lon = -70.0
            offset = not-a-valid-offset
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("BAD_OFFSET", str(ctx.exception))
        self.assertIn("invalid offset", str(ctx.exception))

    def test_config_error_when_waypoint_radius_invalid(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint BAD_RADIUS]
            lat = 43.0
            lon = -71.0
            offset = 4,5,6
            innerRadiusNm = nope
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("BAD_RADIUS", str(ctx.exception))
        self.assertIn("innerradiusnm", str(ctx.exception).lower())

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
        self.assertEqual(1, len(config.waypoints))
        self.assertFalse(config.waypoints[0].hasCoordinates())
        self.assertEqual("", stderr.getvalue())

    def test_config_error_when_waypoint_missing_offset_and_not_hidden(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KEMPTY]
            lat = 40.0
            lon = -73.0
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("KEMPTY", str(ctx.exception))
        self.assertIn("offset", str(ctx.exception).lower())


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
        self.assertEqual(_42fdr_path.resolve().parent, config.airfieldDbPath.resolve().parent)

    def test_airfielddb_max_age_default_and_override(self) -> None:
        default_cfg = _write_temp_config(
            """
            [Defaults]
            """
        )
        overridden_cfg = _write_temp_config(
            """
            [AirfieldDB]
            MaxAgeDays = 30
            """
        )
        default_config = _42fdr.Config(_make_cli_args(default_cfg))
        overridden_config = _42fdr.Config(_make_cli_args(overridden_cfg))
        self.assertAlmostEqual(90.0, default_config.airfieldDbMaxAgeDays)
        self.assertAlmostEqual(30.0, overridden_config.airfieldDbMaxAgeDays)

    def test_airfielddb_maxagedays_sets_max_age(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                MaxAgeDays = 7
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertAlmostEqual(7.0, config.airfieldDbMaxAgeDays)

    def test_airfielddb_path_is_ignored_without_enabled_flag(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                Path = /tmp/OurAirports.csv
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertFalse(config.airfieldDbEnabled)
        self.assertIsNone(config.airfieldDbPath)

    def test_airfielddb_enabled_true_uses_config_path_without_cli_flag(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                enabled = true
                Path = /tmp/OurAirports.csv
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertTrue(config.airfieldDbEnabled)
        self.assertEqual(Path("/tmp/OurAirports.csv"), config.airfieldDbPath)

    def test_airfielddb_flag_prefers_config_path_over_default(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                enabled = false
                Path = /tmp/from-config.csv
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        self.assertTrue(config.airfieldDbEnabled)
        self.assertEqual(Path("/tmp/from-config.csv"), config.airfieldDbPath)

    def test_airfielddb_path_is_ignored_when_cli_path_is_explicit(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                enabled = true
                Path = /tmp/from-config.csv
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db="/tmp/from-cli.csv"))
        self.assertTrue(config.airfieldDbEnabled)
        self.assertEqual(Path("/tmp/from-cli.csv"), config.airfieldDbPath)

    def test_airfielddb_enabled_true_without_path_uses_default_filename(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                enabled = true
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertTrue(config.airfieldDbEnabled)
        self.assertEqual("OurAirports.csv", config.airfieldDbPath.name)
        self.assertEqual(_42fdr_path.resolve().parent, config.airfieldDbPath.resolve().parent)

    def test_waypoints_unknown_key_warns_on_stderr(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [Defaults]
                [AirfieldDB]
                TypoVisitRadius = 3
                """
            )
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(_make_cli_args(cfg_path))
        err = stderr.getvalue().lower()
        self.assertIn("typovisitradius", err)
        self.assertIn("unknown key", err)
        self.assertAlmostEqual(90.0, config.airfieldDbMaxAgeDays)

    def test_airfielddb_dbmaxagedays_is_unknown_key(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [AirfieldDB]
                DBMaxAgeDays = 7
                """
            )
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(_make_cli_args(cfg_path))
        err = stderr.getvalue().lower()
        self.assertIn("dbmaxagedays", err)
        self.assertIn("unknown key", err)
        self.assertAlmostEqual(90.0, config.airfieldDbMaxAgeDays)

    def test_waypoints_visit_radius_overrides(self) -> None:
        cfg_path = _write_temp_config(
            textwrap.dedent(
                """
                [Defaults]
                [AirfieldDB]
                LargeAirportVisitRadius = 9
                DefaultVisitRadius = 3.5
                SeaplaneBaseVisitRadius = 3.5
                """
            )
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))

        def _record(ident: str, record_type: str) -> "_42fdr.OurAirportsRecord":
            rec = _42fdr.OurAirportsRecord.__new__(_42fdr.OurAirportsRecord)
            rec.ident = ident
            rec.gpsCode = ""
            rec.localCode = ""
            rec.iataCode = ""
            rec.lattitude = 40.0
            rec.longitude = -73.0
            rec.name = ident
            rec.type = record_type
            return rec

        config._airfieldRecords = [
            _record("KBIG", "large_airport"),
            _record("KSEA", "seaplane_base"),
        ]
        flight = _42fdr.FdrFlight()
        flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}]
        boxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boxes)
        by_code = {w.code: w for w in waypoints}
        self.assertAlmostEqual(9.0, by_code["KBIG"].visitRadiusNm)
        self.assertAlmostEqual(3.5, by_code["KSEA"].visitRadiusNm)


class AircraftTypeModeTests(unittest.TestCase):
    _CSV_HEADER = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,"
        "iso_country,iso_region,municipality,scheduled_service,gps_code,"
        "iata_code,local_code,home_link,wikipedia_link,keywords\n"
    )
    _CSV_ROWS = {
        "KBIG":    "large_airport",
        "KMED":    "medium_airport",
        "KSML":    "small_airport",
        "KHELI":   "heliport",
        "KBALL":   "balloonport",
        "KSEA":    "seaplane_base",
        "KDEAD":   "closed",
    }
    _EXPECTED_FILTERED = {
        'airplane':   {"KBIG", "KMED", "KSML", "KSEA"},
        'helicopter': {"KBIG", "KMED", "KSML", "KSEA", "KHELI"},
        'balloon':    {"KBIG", "KMED", "KSML", "KSEA", "KBALL"},
    }

    def _write_fixture_db(self) -> Path:
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv")
        handle.write(self._CSV_HEADER)
        for ident, record_type in self._CSV_ROWS.items():
            handle.write(
                f"1,{ident},{record_type},{ident} Name,40.0,-73.0,100,NA,US,US-MA,Town,no,{ident},,,,,\n"
            )
        handle.close()
        return Path(handle.name)

    def _config_with_fixture(
        self,
        defaults_and_sections: str = "[Defaults]\n",
        aircraft_type: Optional[str] = None,
    ) -> "_42fdr.Config":
        db_path = self._write_fixture_db()
        cfg_path = _write_temp_config(defaults_and_sections)
        return _42fdr.Config(
            _make_cli_args(
                cfg_path,
                airfield_db=str(db_path),
                aircraft_type=aircraft_type,
            )
        )

    def test_csv_load_keeps_all_rows_with_valid_coordinates(self) -> None:
        config = self._config_with_fixture()
        idents = {r.ident for r in config._readOurAirportsCsv(config.airfieldDbPath)}
        self.assertEqual(set(self._CSV_ROWS), idents)

    def test_filter_airfields_respects_category(self) -> None:
        config = self._config_with_fixture()
        for cat, expected in self._EXPECTED_FILTERED.items():
            with self.subTest(category=cat):
                got = {r.ident for r in config._filterAirfields([], cat)}
                self.assertEqual(expected, got)

    def test_default_category_matches_airplane_filter(self) -> None:
        config = self._config_with_fixture()
        got = {r.ident for r in config._filterAirfields([])}
        self.assertEqual(self._EXPECTED_FILTERED['airplane'], got)

    def test_aircraft_section_aircraft_type_applies_to_every_tail_in_tails(self) -> None:
        """AircraftType is on the [Aircraft/...] model section, not on [Tail ...]."""
        extra = textwrap.dedent(
            """
            [Aircraft/Heli/R22.acf]
            Tails = N111RC, N222RC
            AircraftType = helicopter
            """
        )
        config = self._config_with_fixture("[Defaults]\n" + extra)
        self.assertEqual('helicopter', config.airfieldCategoryForTail('N111RC'))
        self.assertEqual('helicopter', config.airfieldCategoryForTail('N222RC'))

    def test_waypoints_use_aircraft_section_category_not_defaults(self) -> None:
        extra = textwrap.dedent(
            """
            [Aircraft/Heli/R22.acf]
            Tails = N42HEL
            AircraftType = helicopter
            """
        )
        config = self._config_with_fixture("[Defaults]\n" + extra)
        flight = _42fdr.FdrFlight()
        flight.TAIL = "N42HEL"
        flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}]
        boxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        codes = {w.code for w in config.waypointsForFlight(flight, boxes)}
        self.assertIn("KHELI", codes)
        self.assertNotIn("KBALL", codes)

    def test_unknown_aircraft_section_aircraft_type_raises(self) -> None:
        extra = textwrap.dedent(
            """
            [Aircraft/Weird/Jet.acf]
            Tails = N42BAD
            AircraftType = jetpack
            """
        )
        config = self._config_with_fixture("[Defaults]\n" + extra)
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            config.airfieldCategoryForTail('N42BAD')
        self.assertIn("jetpack", str(ctx.exception))
        self.assertIn("Aircraft/Weird/Jet.acf", str(ctx.exception))

    def test_unknown_aircraft_type_in_defaults_raises(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            AircraftType = jetpack
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("jetpack", str(ctx.exception))
        self.assertIn("aircraftType", str(ctx.exception))


class WaypointOffsetBlendingTests(unittest.TestCase):
    def test_no_match_returns_none(self) -> None:
        helper = _42fdr.WaypointOffsetHelper()
        helper.addWaypoint("KAAA", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)

        self.assertIsNone(helper._offsetFeetForPosition(1.0, 0.0))
        self.assertIsNone(helper.offsetForPosition(1.0, 0.0))

    def test_inner_radius_blending_uses_inverse_ratio_weights(self) -> None:
        helper = _42fdr.WaypointOffsetHelper()
        helper.addWaypoint("A", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)
        helper.addWaypoint("B", 0.0, 1.0 / 60.0, _42fdr.CardinalOffset(20.0, 0.0, 0.0), 2.0, 4.0)

        # Near A (0.25 NM from A, 0.75 NM from B) => A should dominate 3:1 by inverse ratio.
        blended = helper._offsetFeetForPosition(0.0, 0.25 / 60.0)
        self.assertIsNotNone(blended)
        self.assertAlmostEqual(12.5, blended.eastFt, places=1)

    def test_outer_ring_tapers_with_distance(self) -> None:
        helper = _42fdr.WaypointOffsetHelper()
        helper.addWaypoint("KAAA", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)

        # At 3 NM in a 2-4 NM ring => local weight (4-3)/(4-2) = 0.5 => east offset 5 ft.
        blended = helper._offsetFeetForPosition(0.0, 3.0 / 60.0)
        self.assertIsNotNone(blended)
        self.assertAlmostEqual(5.0, blended.eastFt, places=1)

    def test_overlapping_outer_entries_blend_deterministically(self) -> None:
        helper = _42fdr.WaypointOffsetHelper()
        helper.addWaypoint("A", 0.0, 0.0, _42fdr.CardinalOffset(10.0, 0.0, 0.0), 2.0, 4.0)
        helper.addWaypoint("B", 0.0, 0.0, _42fdr.CardinalOffset(30.0, 0.0, 0.0), 2.0, 4.0)

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

        boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boundingBoxes)
        helper = config.offsetHelperFrom(waypoints)
        self.assertEqual(3, len(helper._waypoints))

        by_code = {entry.code: entry for entry in helper._waypoints}
        self.assertIn("KCFG", by_code)
        self.assertIn("KORIG", by_code)
        self.assertIn("KDEST", by_code)
        self.assertAlmostEqual(1.0, by_code["KORIG"].offset.eastFt)
        self.assertAlmostEqual(8.0, by_code["KDEST"].offset.northFt)
        self.assertAlmostEqual(4.0, by_code["KCFG"].offset.eastFt)

    def test_cli_offset_merges_into_nearest_waypoint_when_inside_inner(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KCFG]
            lat = 40.05
            lon = -73.05
            offset = 4,5,6
            """
        )
        config = _42fdr.Config(_make_cli_args(config_path=cfg_path, offset_orig="1,2,3"))
        flight = _42fdr.FdrFlight()
        flight.metaData = _42fdr.FlightMeta()
        flight.trackData = [
            {"Latitude": 40.051, "Longitude": -73.051},
            {"Latitude": 41.0, "Longitude": -72.0},
        ]
        boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boundingBoxes)
        helper = config.offsetHelperFrom(waypoints)
        by_code = {entry.code: entry for entry in helper._waypoints}
        self.assertEqual(1, len(by_code))
        k = by_code["KCFG"]
        self.assertAlmostEqual(5.0, k.offset.eastFt)
        self.assertAlmostEqual(7.0, k.offset.northFt)
        self.assertAlmostEqual(9.0, k.offset.upFt)

    def test_cli_both_offsets_same_inner_zone_averages_onto_waypoint(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KCFG]
            lat = 40.05
            lon = -73.05
            offset = 4,5,6
            """
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(
                _make_cli_args(
                    config_path=cfg_path,
                    offset_orig="2,0,0",
                    offset_dest="4,0,0",
                )
            )
            flight = _42fdr.FdrFlight()
            flight.metaData = _42fdr.FlightMeta()
            flight.trackData = [
                {"Latitude": 40.051, "Longitude": -73.051},
                {"Latitude": 40.0515, "Longitude": -73.0512},
            ]
            boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
            waypoints = config.waypointsForFlight(flight, boundingBoxes)
            helper = config.offsetHelperFrom(waypoints)
        self.assertIn("same inner zone", stderr.getvalue())
        by_code = {entry.code: entry for entry in helper._waypoints}
        self.assertEqual(1, len(by_code))
        k = by_code["KCFG"]
        # Avg(2,0,0) + (4,0,0) = (3,0,0) on top of (4,5,6) => (7,5,6)
        self.assertAlmostEqual(7.0, k.offset.eastFt)
        self.assertAlmostEqual(5.0, k.offset.northFt)
        self.assertAlmostEqual(6.0, k.offset.upFt)

    def test_cli_close_endpoints_get_local_cli_midpoint(self) -> None:
        cfg_path = _write_temp_config("\n[Defaults]\n")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(
                _make_cli_args(
                    config_path=cfg_path,
                    offset_orig="0,0,10",
                    offset_dest="0,0,20",
                )
            )
            flight = _42fdr.FdrFlight()
            flight.metaData = _42fdr.FlightMeta()
            flight.trackData = [
                {"Latitude": 40.0, "Longitude": -73.0},
                {"Latitude": 40.01, "Longitude": -73.01},
            ]
            boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
            waypoints = config.waypointsForFlight(flight, boundingBoxes)
            helper = config.offsetHelperFrom(waypoints)
        self.assertIn("HOME", stderr.getvalue())
        by_code = {entry.code: entry for entry in helper._waypoints}
        self.assertIn("HOME", by_code)
        self.assertAlmostEqual(15.0, by_code["HOME"].offset.upFt)

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
        boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boundingBoxes)
        helper = config.offsetHelperFrom(waypoints)
        by_code = {entry.code: entry for entry in helper._waypoints}
        self.assertIn("KNEAR", by_code)
        self.assertNotIn("KFAR", by_code)


class HideFromRouteTests(unittest.TestCase):
    def test_hideFromRoute_default_false_and_parsed(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KVIS]
            lat = 40.0
            lon = -73.0
            offset = 1,2,3

            [Waypoint KHID]
            lat = 41.0
            lon = -74.0
            offset = 4,5,6
            hideFromRoute = true
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        by_code = {entry.code: entry for entry in config.waypoints}
        self.assertFalse(by_code["KVIS"].hideFromRoute)
        self.assertTrue(by_code["KHID"].hideFromRoute)

    def test_hideFromRoute_bare_key_means_true(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KBARE]
            lat = 40.0
            lon = -73.0
            offset = 1,2,3
            hideFromRoute
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        by_code = {entry.code: entry for entry in config.waypoints}
        self.assertTrue(by_code["KBARE"].hideFromRoute)

    def test_hideFromRoute_invalid_raises_with_section_in_message(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KBAD]
            lat = 40.0
            lon = -73.0
            offset = 1,2,3
            hideFromRoute = maybe
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("hideFromRoute", str(ctx.exception))
        self.assertIn("Waypoint KBAD", str(ctx.exception))

    def test_hide_only_loads_with_offset_none_when_airfield_db_enabled(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KHIDE]
            hideFromRoute = true
            """
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        self.assertEqual("", stderr.getvalue())
        self.assertEqual(1, len(config.waypoints))
        w = config.waypoints[0]
        self.assertEqual("KHIDE", w.code)
        self.assertTrue(w.hideFromRoute)
        self.assertIsNone(w.offset)
        self.assertFalse(w.hasCoordinates())

    def test_hide_only_loads_without_coords_when_airfield_db_disabled(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KHIDE_LOCAL]
            hideFromRoute = true
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertEqual(1, len(config.waypoints))
        w = config.waypoints[0]
        self.assertEqual("KHIDE_LOCAL", w.code)
        self.assertTrue(w.hideFromRoute)
        self.assertIsNone(w.offset)
        self.assertFalse(w.hasCoordinates())

    def test_hideFromRoute_hidden_waypoint_excluded_from_derived_route(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute

            [Waypoint KSHOW]
            lat = 40.0
            lon = -73.0
            offset = 1,1,1
            innerRadiusNm = 5.0

            [Waypoint KHIDE]
            lat = 40.0
            lon = -73.0
            offset = 1,1,1
            innerRadiusNm = 5.0
            hideFromRoute = true
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        flight = _42fdr.FdrFlight()
        flight.TAIL = "TEST"
        flight.timezone = 0
        flight.metaData = _42fdr.FlightMeta()
        flight.trackData = [
            {"Timestamp": 0, "Latitude": 40.0, "Longitude": -73.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
        ]
        flight.buildTrackPoints(config)
        self.assertEqual(["KSHOW"], flight.metaData.DerivedRoute)

    def test_hide_only_resolves_from_db_merges_once_excluded_from_offset_helper(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KHIDE2]
            hideFromRoute = true
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        config._airfieldRecords = [
            _42fdr.OurAirportsRecord.__new__(_42fdr.OurAirportsRecord),
        ]
        rec = config._airfieldRecords[0]
        rec.ident = "KHIDE2"
        rec.gpsCode = ""
        rec.localCode = ""
        rec.iataCode = ""
        rec.lattitude = 40.0
        rec.longitude = -73.0
        rec.name = "Hideme"
        rec.type = "small_airport"

        flight = _42fdr.FdrFlight()
        flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}]
        boxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boxes)
        by_code = {w.code: w for w in waypoints}
        self.assertEqual(1, sum(1 for w in waypoints if w.code == "KHIDE2"))
        self.assertIn("KHIDE2", by_code)
        self.assertTrue(by_code["KHIDE2"].hideFromRoute)
        self.assertIsNone(by_code["KHIDE2"].offset)
        self.assertAlmostEqual(40.0, by_code["KHIDE2"].lattitude)
        helper = config.offsetHelperFrom(waypoints)
        by_helper = {e.code: e for e in helper._waypoints}
        self.assertNotIn("KHIDE2", by_helper)



class EnableRoutingDefaultsTests(unittest.TestCase):
    def test_enable_routing_default_false_when_absent(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertFalse(config.enableRouting)

    def test_enable_routing_bare_key_means_true(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertTrue(config.enableRouting)

    def test_enable_routing_empty_equals_means_true(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute =
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertTrue(config.enableRouting)

    def test_enable_routing_explicit_false(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute = false
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        self.assertFalse(config.enableRouting)

    def test_enable_routing_invalid_raises(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute = maybe
            """
        )
        with self.assertRaises(_42fdr.ConfigError) as ctx:
            _42fdr.Config(_make_cli_args(cfg_path))
        self.assertIn("inferRoute", str(ctx.exception))

    def test_enable_routing_off_skips_derived_route(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute = false

            [Waypoint KSHOW]
            lat = 40.0
            lon = -73.0
            offset = 1,1,1
            innerRadiusNm = 5.0
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        flight = _42fdr.FdrFlight()
        flight.TAIL = "TEST"
        flight.timezone = 0
        flight.metaData = _42fdr.FlightMeta()
        flight.trackData = [
            {"Timestamp": 0, "Latitude": 40.0, "Longitude": -73.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
        ]
        flight.buildTrackPoints(config)
        self.assertIsNone(flight.metaData.DerivedRoute)


class DerivedRouteTests(unittest.TestCase):
    def test_db_record_fills_missing_configured_waypoint_coords(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Waypoint KDBF]
            offset = 1,2,3
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
        # Seed the in-memory airfield cache so the DB loader doesn't try network I/O.
        config._airfieldRecords = [
            _42fdr.OurAirportsRecord.__new__(_42fdr.OurAirportsRecord),
        ]
        record = config._airfieldRecords[0]
        record.ident = "KDBF"
        record.gpsCode = ""
        record.localCode = ""
        record.iataCode = ""
        record.lattitude = 40.0
        record.longitude = -73.0
        record.name = "DB Filled"
        record.type = "small_airport"

        flight = _42fdr.FdrFlight()
        flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}]
        boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(flight, boundingBoxes)
        by_code = {entry.code: entry for entry in waypoints}
        self.assertIn("KDBF", by_code)
        self.assertAlmostEqual(40.0, by_code["KDBF"].lattitude)
        self.assertAlmostEqual(-73.0, by_code["KDBF"].longitude)
        self.assertAlmostEqual(1.0, by_code["KDBF"].offset.eastFt)

    def test_db_record_visit_radius_varies_by_airfield_type(self) -> None:
        def _record(ident: str, recordType: str) -> "_42fdr.OurAirportsRecord":
            rec = _42fdr.OurAirportsRecord.__new__(_42fdr.OurAirportsRecord)
            rec.ident = ident
            rec.gpsCode = ""
            rec.localCode = ""
            rec.iataCode = ""
            rec.lattitude = 40.0
            rec.longitude = -73.0
            rec.name = ident
            rec.type = recordType
            return rec

        seeded = [
            _record("KBIG", "large_airport"),
            _record("KMED", "medium_airport"),
            _record("KSML", "small_airport"),
            _record("KHEL", "heliport"),
            _record("KBAL", "balloonport"),
        ]
        cases = [
            (
                "airplane",
                "[Defaults]\n",
                "ANY",
                {"KBIG", "KMED", "KSML"},
            ),
            (
                "helicopter",
                textwrap.dedent(
                    """
                    [Defaults]
                    [Aircraft/Heli/R22.acf]
                    Tails = NHEL
                    AircraftType = helicopter
                    """
                ),
                "NHEL",
                {"KBIG", "KMED", "KSML", "KHEL"},
            ),
            (
                "balloon",
                textwrap.dedent(
                    """
                    [Defaults]
                    [Aircraft/Balloon/Fire.acf]
                    Tails = NBAL
                    AircraftType = balloon
                    """
                ),
                "NBAL",
                {"KBIG", "KMED", "KSML", "KBAL"},
            ),
        ]

        for label, ini, tail, expected_codes in cases:
            with self.subTest(mode=label):
                cfg_path = _write_temp_config(ini)
                config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))
                config._airfieldRecords = list(seeded)
                vr = config.airfieldTypeVisitRadiusNm
                fd = config.airfieldDefaultVisitRadiusNm
                by_ident = {r.ident: r for r in seeded}
                expected = {
                    ident: vr.get(by_ident[ident].type, fd)
                    for ident in expected_codes
                }
                flight = _42fdr.FdrFlight()
                flight.TAIL = tail
                flight.trackData = [{"Latitude": 40.0, "Longitude": -73.0}]
                boundingBoxes = flight._buildBoundingBoxes(config.airfieldGridCellNm)
                waypoints = config.waypointsForFlight(flight, boundingBoxes)
                by_code = {entry.code: entry for entry in waypoints}
                self.assertEqual(set(expected), set(by_code))
                for code, radius in expected.items():
                    self.assertAlmostEqual(radius, by_code[code].visitRadiusNm)

    def test_derived_route_ignores_small_fields_just_outside_tight_radius(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path, airfield_db=""))

        def _record(ident: str, recordType: str, lat: float, lon: float):
            rec = _42fdr.OurAirportsRecord.__new__(_42fdr.OurAirportsRecord)
            rec.ident = ident
            rec.gpsCode = ""
            rec.localCode = ""
            rec.iataCode = ""
            rec.lattitude = lat
            rec.longitude = lon
            rec.name = ident
            rec.type = recordType
            return rec

        # KBIG is a large airport at origin; KNOISE is a small field ~1.5 NM away
        # (outside small_airport's new 1 NM radius but would have matched the old 2 NM radius).
        config._airfieldRecords = [
            _record("KBIG", "large_airport", 40.0, -73.0),
            _record("KNOISE", "small_airport", 40.0 + 1.5 / 60.0, -73.0),
        ]

        flight = _42fdr.FdrFlight()
        flight.TAIL = "TEST"
        flight.timezone = 0
        flight.metaData = _42fdr.FlightMeta()
        # Aircraft passes through KBIG's center while always staying ~1.5 NM
        # from KNOISE. Under the old 2 NM default KNOISE would also be listed.
        flight.trackData = [
            {"Timestamp": 0,  "Latitude": 40.0, "Longitude": -73.0 - 0.5 / 60.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
            {"Timestamp": 60, "Latitude": 40.0, "Longitude": -73.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
            {"Timestamp": 120,"Latitude": 40.0, "Longitude": -73.0 + 0.5 / 60.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
        ]
        flight.buildTrackPoints(config)
        self.assertEqual(["KBIG"], flight.metaData.DerivedRoute)

    def test_derived_route_collapses_touch_and_go_repeats(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute

            [Waypoint KORH]
            lat = 42.2673
            lon = -71.8757
            offset = 1,1,1
            innerRadiusNm = 1.0

            [Waypoint KBED]
            lat = 42.4700
            lon = -71.2890
            offset = 1,1,1
            innerRadiusNm = 1.0
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        flight = _42fdr.FdrFlight()
        flight.TAIL = "TEST"
        flight.timezone = 0
        flight.metaData = _42fdr.FlightMeta()

        def _pt(t: float, lat: float, lon: float) -> dict:
            return {"Timestamp": t, "Latitude": lat, "Longitude": lon,
                    "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0}

        # Depart KBED, fly to KORH, do three pattern laps (repeatedly leaving
        # and re-entering the 1 NM radius), then return to KBED. The laps must
        # not show up as multiple KORH entries in the derived route.
        KORH_LAT = 42.2673
        KORH_LON = -71.8757
        OUTSIDE_OFFSET_NM = 1.5 / 60.0  # 1.5 NM north of KORH, outside the 1 NM radius

        track = [_pt(0, 42.4700, -71.2890)]  # at KBED
        t = 60.0
        for _ in range(3):
            track.append(_pt(t, KORH_LAT, KORH_LON)); t += 60
            track.append(_pt(t, KORH_LAT + OUTSIDE_OFFSET_NM, KORH_LON)); t += 60
        track.append(_pt(t, KORH_LAT, KORH_LON)); t += 60  # final touchdown
        track.append(_pt(t, 42.4700, -71.2890))  # back at KBED
        flight.trackData = track

        flight.buildTrackPoints(config)
        self.assertEqual(["KBED", "KORH", "KBED"], flight.metaData.DerivedRoute)

    def test_derived_route_transitions_through_waypoints(self) -> None:
        cfg_path = _write_temp_config(
            """
            [Defaults]
            inferRoute

            [Waypoint KA]
            lat = 40.0
            lon = -73.0
            offset = 1,1,1
            innerRadiusNm = 1.0

            [Waypoint KB]
            lat = 40.0
            lon = -72.0
            offset = 1,1,1
            innerRadiusNm = 1.0
            """
        )
        config = _42fdr.Config(_make_cli_args(cfg_path))
        flight = _42fdr.FdrFlight()
        flight.TAIL = "TEST"
        flight.timezone = 0
        flight.metaData = _42fdr.FlightMeta()
        flight.trackData = [
            {"Timestamp": 0, "Latitude": 40.0, "Longitude": -73.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
            {"Timestamp": 60, "Latitude": 40.0, "Longitude": -72.5,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
            {"Timestamp": 120, "Latitude": 40.0, "Longitude": -72.0,
             "Altitude": 0, "Course": 0, "Pitch": 0, "Bank": 0, "Speed": 0},
        ]
        flight.buildTrackPoints(config)
        self.assertEqual(["KA", "KB"], flight.metaData.DerivedRoute)


class FlightBoundingBoxTests(unittest.TestCase):
    def test_build_bounding_boxes_partitions_long_tracks(self) -> None:
        flight = _42fdr.FdrFlight()
        # About 420 NM east-west with a 100 NM grid should create multiple boxes.
        flight.trackData = [
            {"Latitude": 40.0, "Longitude": -80.0},
            {"Latitude": 40.0, "Longitude": -74.0},
            {"Latitude": 40.0, "Longitude": -68.0},
        ]
        boundingBoxes = flight._buildBoundingBoxes(100.0)
        self.assertGreaterEqual(len(boundingBoxes), 3)


if __name__ == "__main__":
    unittest.main()
