#!/usr/bin/env python3
import argparse, configparser, csv, os, re, sys, tempfile, xml.etree.ElementTree as ET
import math  # Used when evaluating user DREF value expressions
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, Tuple, Union
from urllib import error as urlerror
from urllib import request as urlrequest


FdrColumnWidth = 19


class ConfigError(ValueError):
    """Invalid user configuration (``42fdr.conf`` or command-line). Fatal in ``__main__``."""


class AircraftConfigError(ConfigError):
    """Invalid aircraft/tail-specific configuration encountered while processing a track file."""


class FileType(Enum):
    UNKNOWN = 0
    CSV = 1
    KML = 2
    GPX = 3


class CardinalOffset:
    """Local offset in feet (east, north, up) used for waypoint-based nudging."""
    eastFt: float
    northFt: float
    upFt: float

    def __init__(self, eastFt: float, northFt: float, upFt: float):
        self.eastFt = eastFt
        self.northFt = northFt
        self.upFt = upFt

    @classmethod
    def zero(cls) -> "CardinalOffset":
        return cls(0.0, 0.0, 0.0)

    @classmethod
    def fromString(cls, s: str) -> "CardinalOffset":
        """Parse ``east, north, up`` with optional sign on each (all feet)."""
        m = Config._XYZ_OFFSET_RE.match(s.strip())
        if not m:
            raise ValueError(
                f"invalid offset {s!r}: expected east,north,up in feet (three comma-separated numbers, optional +/- per value)"
            )
        return cls(float(m.group(1)), float(m.group(2)), float(m.group(3)))


    def __add__(self, other: Optional["CardinalOffset"]) -> "CardinalOffset":
        if other is None:
            return self
        if not isinstance(other, CardinalOffset):
            return NotImplemented
        return CardinalOffset(
            self.eastFt + other.eastFt,
            self.northFt + other.northFt,
            self.upFt + other.upFt,
        )

    def __radd__(self, other: Optional["CardinalOffset"]) -> "CardinalOffset":
        if other is None:
            return self
        if not isinstance(other, CardinalOffset):
            return NotImplemented
        return other + self

    def averageWith(self, other: "CardinalOffset") -> "CardinalOffset":
        return CardinalOffset(
            0.5 * (self.eastFt + other.eastFt),
            0.5 * (self.northFt + other.northFt),
            0.5 * (self.upFt + other.upFt),
        )

    def approxEqual(self, other: "CardinalOffset", eps: float = 1e-6) -> bool:
        return (
            abs(self.eastFt - other.eastFt) <= eps
            and abs(self.northFt - other.northFt) <= eps
            and abs(self.upFt - other.upFt) <= eps
        )


class GeodeticOffset:
    """Rendered offset in degrees/feet (lat, lon, altitude) used for waypoint-based nudging."""
    deltaLatitude: float
    deltaLongitude: float
    deltaAltitude: float

    def __init__(self, deltaLatitude: float, deltaLongitude: float, deltaAltitude: float):
        self.deltaLatitude = deltaLatitude
        self.deltaLongitude = deltaLongitude
        self.deltaAltitude = deltaAltitude


class Config():
    file:Optional[configparser.RawConfigParser] = None

    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf'
    outPath:str = '.'
    aircraftType: str = 'airplane'
    timezone:float = 0
    timezoneCSV:Optional[float] = None
    timezoneKML:Optional[float] = None
    offsetOrig: Optional[CardinalOffset] = None
    offsetDest: Optional[CardinalOffset] = None

    waypoints: List["WaypointEntry"]
    airfieldDbPath: Optional[Path] = None
    airfieldDbEnabled: bool = False
    enableRouting: bool = False
    airfieldDbMaxAgeDays: float = 90.0
    airfieldGridCellNm: float = 120.0
    airfieldDefaultVisitRadiusNm: float
    airfieldTypeVisitRadiusNm: Dict[str, float]
    _airfieldRecords: Optional[List["OurAirportsRecord"]]

    AIRFIELD_DB_DEFAULT_FILENAME = 'OurAirports.csv'
    AIRFIELD_DB_URL = 'https://davidmegginson.github.io/ourairports-data/airports.csv'

    OFFSET_INNER_RADIUS_NM = 2.0
    OFFSET_OUTER_RADIUS_NM = 6.0

    # Airport types considered for a given aircraft type.
    AIRCRAFT_TYPE_DEFAULT = 'airplane'
    AIRFIELD_TYPES_BY_AIRCRAFT = {
        'airplane': frozenset({'large_airport', 'medium_airport', 'small_airport', 'seaplane_base'}),
        'helicopter': frozenset({'large_airport', 'medium_airport', 'small_airport', 'seaplane_base', 'heliport'}),
        'balloon': frozenset({'large_airport', 'medium_airport', 'small_airport', 'seaplane_base', 'balloonport'}),
    }

    # Per-type visit radius (route detection) for OurAirports-sourced waypoints.
    AIRFIELD_DEFAULT_VISIT_RADIUS_NM = 1.0
    AIRFIELD_TYPE_VISIT_RADIUS_NM = {
        'large_airport':  2.0,
        'medium_airport': 1.00,
        'small_airport':  0.75,
        'seaplane_base':  1.00,
        'balloonport':    0.25,
        'heliport':       0.05,
    }

    # [AirfieldDB] keys (lower-cased by ConfigParser) -> OurAirports ``type`` string.
    _AIRFIELDS_VISIT_RADIUS_OPTION_TO_TYPE = {
        'largeairportvisitradius': 'large_airport',
        'mediumairportvisitradius': 'medium_airport',
        'smallairportvisitradius': 'small_airport',
        'heliportvisitradius': 'heliport',
        'balloonportvisitradius': 'balloonport',
        'seaplanebasevisitradius': 'seaplane_base',
    }

    _TAIL_SECTION_PREFIX = 'tail '
    _WAYPOINT_SECTION_PREFIX = 'waypoint '
    _XYZ_OFFSET_RE = re.compile(
        r'^\s*([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*$'
    )


    def __init__(self, cliArgs:argparse.Namespace):
        self.file = configparser.RawConfigParser(inline_comment_prefixes=(';'), allow_no_value=True)
        configFile = self._findConfigFile(cliArgs.config)
        if configFile:
            self.file.read(configFile)

        defaults = self.file['Defaults'] if 'Defaults' in self.file else {}

        self.cliAircraft = False
        if cliArgs.aircraft:
            self.aircraft = cliArgs.aircraft.replace('\\', '/')
            self.cliAircraft = True
        elif 'aircraft' in defaults:
            self.aircraft = defaults['aircraft'].replace('\\', '/')

        if cliArgs.aircraftType:
            self.aircraftType = cliArgs.aircraftType
        elif 'aircrafttype' in defaults:
            self.aircraftType = defaults['aircrafttype'].strip().lower()
        else:
            self.aircraftType = self.AIRCRAFT_TYPE_DEFAULT
        if self.aircraftType not in self.AIRFIELD_TYPES_BY_AIRCRAFT:
            allowed = ', '.join(sorted(self.AIRFIELD_TYPES_BY_AIRCRAFT))
            raise ConfigError(
                f"Unknown aircraftType {self.aircraftType!r}. Expected one of: {allowed}."
            )

        if cliArgs.timezone:
            self.timezone = timezoneOffsetInSeconds(cliArgs.timezone)
        else:
            if 'timezone' in defaults:
                self.timezone = self._parseTimezone(defaults, 'timezone')
            if 'timezonecsv' in defaults:
                self.timezoneCSV = self._parseTimezone(defaults, 'timezonecsv')
            if 'timezonekml' in defaults:
                self.timezoneKML = self._parseTimezone(defaults, 'timezonekml')

        if cliArgs.outputFolder:
            self.outPath = os.path.expanduser(cliArgs.outputFolder)
        elif 'outpath' in defaults:
            self.outPath = os.path.expanduser(defaults['outpath'])
        if not os.path.isdir(self.outPath):
            raise ConfigError(f"Output folder does not exist: {self.outPath}")

        self.airfieldDefaultVisitRadiusNm = self.AIRFIELD_DEFAULT_VISIT_RADIUS_NM
        self.airfieldTypeVisitRadiusNm = dict(self.AIRFIELD_TYPE_VISIT_RADIUS_NM)
        self._applyAirfieldDbSection(cliArgs.airfieldDB)
        self._airfieldRecords = None

        if cliArgs.inferRoute:
            self.enableRouting = True
        else:
            self.enableRouting = self._parseEnableFlag(defaults, 'inferRoute')

        self.waypoints = self._loadWaypoints()
        if cliArgs.offsetOrig:
            try:
                self.offsetOrig = CardinalOffset.fromString(cliArgs.offsetOrig)
            except ValueError as err:
                raise ConfigError(f"Invalid --offsetOrig value: {err}") from None
        if cliArgs.offsetDest:
            try:
                self.offsetDest = CardinalOffset.fromString(cliArgs.offsetDest)
            except ValueError as err:
                raise ConfigError(f"Invalid --offsetDest value: {err}") from None


    def aircraftPathForTail(self, tailNumber: str) -> str:
        """Path to aircraft model file for this tail number."""
        section = self._aircraftByTail(tailNumber)
        return section.replace('\\', '/') if section else self.aircraft


    def airfieldCategoryForTail(self, tailNumber: str) -> str:
        """Airfield category for OurAirports filtering for this tail.

        Uses ``AircraftType`` from the matched ``[Aircraft/...]`` section when set,
        otherwise uses the global ``aircraftType`` from config/CLI.
        """
        if self.file:
            section = self._aircraftByTail(tailNumber)
            if section in self.file:
                aircraft = self.file[section]
                override = aircraft.get('AircraftType')
                if override is not None:
                    resolved = override.strip().lower()
                    if resolved in self.AIRFIELD_TYPES_BY_AIRCRAFT:
                        return resolved
                    allowed = ', '.join(sorted(self.AIRFIELD_TYPES_BY_AIRCRAFT))
                    raise AircraftConfigError(
                        f"Unknown AircraftType {resolved!r} in [{section}]. Expected one of: {allowed}."
                    )
        return self.aircraftType


    def drefsByTail(self, tailNumber: str) -> Tuple[Dict[str, str], List[str]]:
        """Collect DREF definitions from all relevant sections."""
        sources: Dict[str, str] = {}
        defines: List[str] = []

        def add(instrument: str, value: str, scale: str = '1.0', name: Optional[str] = None):
            name = name or instrument.rpartition('/')[2][:FdrColumnWidth]
            sources[name] = value
            defines.append(f'{instrument}\t{scale}\t\t// source: {value}')

        def parseDrefsInSection(sectionName: str):
            if self.file and sectionName in self.file:
                for key, val in self.file[sectionName].items():
                    if key.lower().startswith('dref '):
                        instrument, expr, scale, name = parseDrefEntry(key, val)
                        add(instrument, expr, scale, name)

        def parseDrefEntry(key: str, val: str) -> Tuple[str, str, str, Optional[str]]:
            if not key.lower().startswith('dref '):
                raise AircraftConfigError(f"Invalid DREF key (must start with 'DREF '): {key}")

            instrument = key[5:].strip().replace('\\', '/')

            exprEnd = None
            depth = 0
            for i, char in enumerate(val):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth < 0:
                        raise AircraftConfigError(f"Unmatched closing parenthesis in DREF expression: {val}")
                elif char == ',' and depth == 0:
                    exprEnd = i
                    break

            if depth != 0:
                raise AircraftConfigError(f"Unmatched parenthesis in DREF expression: {val}")

            if exprEnd is not None:
                expr = val[:exprEnd].strip()
                rest = [x.strip() for x in val[exprEnd+1:].split(',', 1)]
                scale = rest[0] if len(rest) > 0 else '1.0'
                name = rest[1] if len(rest) > 1 else None
            else:
                expr = val.strip()
                scale = '1.0'
                name = None

            return instrument, expr, scale, name

        # Always include the default ground speed DREF
        add('sim/cockpit2/gauges/indicators/ground_speed_kt', 'round({Speed}, 4)', '1.0', 'GndSpd')

        tailSection = self._tailSectionFor(tailNumber)
        parseDrefsInSection('Defaults')
        parseDrefsInSection(self._aircraftByTail(tailNumber))
        parseDrefsInSection(tailNumber)
        if tailSection is not None:
            parseDrefsInSection(tailSection)

        return sources, defines


    _TAIL_TRIM_KEYS = frozenset[str]({'headingtrim', 'pitchtrim', 'rolltrim'})
    def tailConfigFor(self, tailNumber:str) -> dict[str, Any]:
        """Load tail config from [Tail <TailNumber>] and legacy [<TailNumber>] sections."""
        tailConfig = {}
        tailSectionName = self._tailSectionFor(tailNumber)
        if self.file:
            for section in self.file.sections():
                if section.lower() == tailNumber.lower():
                    tailSection = self.file[section]
                    for key in self.file[section]:
                        valueString = tailSection[key]
                        if key.lower() in self._TAIL_TRIM_KEYS:
                            tailConfig[key] = float(valueString)
                        else:
                            tailConfig[key] = valueString
                    break

            if tailSectionName is not None:
                section = self.file[tailSectionName]
                for key in section:
                    valueString = section[key]
                    if key.lower() in self._TAIL_TRIM_KEYS:
                        tailConfig[key] = float(valueString)
                    else:
                        tailConfig[key] = valueString

        if 'headingtrim' not in tailConfig:
            tailConfig['headingtrim'] = 0
        if 'pitchtrim' not in tailConfig:
            tailConfig['pitchtrim'] = 0
        if 'rolltrim' not in tailConfig:
            tailConfig['rolltrim'] = 0

        return tailConfig


    def waypointsForFlight(
        self,
        flight: "FdrFlight",
        boundingBoxes: List["BoundingBox"],
    ) -> List["WaypointEntry"]:
        airfieldCategory = self.airfieldCategoryForTail(flight.TAIL)
        nearbyAirfields = self._filterAirfields(boundingBoxes, airfieldCategory)
        airfieldLookupMap = self._airfieldLookupMap(nearbyAirfields)

        merged: List[WaypointEntry] = []
        seenCodes: set = set()

        for waypoint in self.waypoints:
            resolved = self._resolveWaypoint(waypoint, airfieldLookupMap)
            if resolved is not None and resolved.lattitude is not None and resolved.longitude is not None:
                if self._isInFlightBounds(
                    resolved.lattitude,
                    resolved.longitude,
                    boundingBoxes,
                    resolved.outerRadiusNm,
                ):
                    merged.append(resolved)
                    seenCodes.add(resolved.code.upper())

        for record in nearbyAirfields:
            code = record.ident.strip()
            if not code:
                continue

            codeUpper = code.upper()
            if codeUpper not in seenCodes:
                visitRadiusNm = self.airfieldTypeVisitRadiusNm.get(
                    record.type, self.airfieldDefaultVisitRadiusNm
                )
                merged.append(
                    WaypointEntry(
                        code=code,
                        offset=None,
                        innerRadiusNm=self.OFFSET_INNER_RADIUS_NM,
                        outerRadiusNm=self.OFFSET_OUTER_RADIUS_NM,
                        visitRadiusNm=visitRadiusNm,
                        lattitude=record.lattitude,
                        longitude=record.longitude,
                    )
                )
                seenCodes.add(codeUpper)

        self._applyCliOffsetsToWaypoints(flight, merged, seenCodes)

        return merged


    def offsetHelperFrom(
        self,
        waypoints: List["WaypointEntry"],
    ) -> "WaypointOffsetHelper":
        """Create a helper for calculating waypoint offsets."""
        offsetHelper = WaypointOffsetHelper()
        for entry in waypoints:
            if entry.offset is not None:
                if entry.lattitude is not None and entry.longitude is not None:
                    offsetHelper.addWaypoint(
                        code=entry.code,
                        lattitude=entry.lattitude,
                        longitude=entry.longitude,
                        offset=entry.offset,
                        innerRadiusNm=entry.innerRadiusNm,
                        outerRadiusNm=entry.outerRadiusNm,
                    )
        return offsetHelper


    def _aircraftByTail(self, tailNumber: str) -> str:
        """Section name for ``[Aircraft/...]`` when ``tailNumber`` is in ``Tails``.
        If no section is found, return the default aircraft.
        """
        tail = (tailNumber or '').strip()
        if self.cliAircraft or not self.file or not tail:
            return self.aircraft
        for section in self.file.sections():
            pathNorm = section.lower().replace('\\', '/')
            if pathNorm.startswith('aircraft/'):
                aircraft = self.file[section]
                tails_raw = aircraft.get('Tails')
                if tails_raw:
                    registrations = [t.strip() for t in tails_raw.split(',') if t.strip()]
                    if tail in registrations:
                        return section
        return self.aircraft


    def _tailSectionFor(self, tailNumber: str) -> Optional[str]:
        if not self.file:
            return None

        match = f'{self._TAIL_SECTION_PREFIX}{tailNumber}'.lower()
        for section in self.file.sections():
            if section.lower() == match:
                return section
        return None


    def _appendSyntheticCliWaypoint(
        self,
        merged: List["WaypointEntry"],
        seenCodes: set,
        lattitude: float,
        longitude: float,
        offset: CardinalOffset,
        code: str,
    ) -> None:
        codeUpper = code.strip().upper() or "ORIG"
        if codeUpper in seenCodes:
            for suffix in range(2, 1000):
                candidate = f"{code}-{suffix}"
                if candidate.upper() not in seenCodes:
                    code = candidate
                    codeUpper = code.upper()
                    break
        merged.append(
            WaypointEntry(
                code=code,
                offset=offset,
                innerRadiusNm=self.OFFSET_INNER_RADIUS_NM,
                outerRadiusNm=self.OFFSET_OUTER_RADIUS_NM,
                lattitude=lattitude,
                longitude=longitude,
            )
        )
        seenCodes.add(codeUpper)


    def _applyCliOffsetAtPosition(
        self,
        flightMeta: Optional["FlightMeta"],
        isOrigin: bool,
        latitude: float,
        longitude: float,
        offset: CardinalOffset,
        merged: List["WaypointEntry"],
        seenCodes: set,
    ) -> None:
        match = self._nearestWaypoint(latitude, longitude, merged)
        if match is not None:
            match.offset = match.offset + offset
            return
        if isOrigin:
            code = (flightMeta.DerivedOrigin if flightMeta and flightMeta.DerivedOrigin else "ORIG").strip() or "ORIG"
        else:
            code = (flightMeta.DerivedDestination if flightMeta and flightMeta.DerivedDestination else "DEST").strip() or "DEST"
        self._appendSyntheticCliWaypoint(merged, seenCodes, latitude, longitude, offset, code)


    def _applyCliOffsetsToWaypoints(
        self,
        flight: "FdrFlight",
        waypoints: List["WaypointEntry"],
        seenCodes: set,
    ) -> None:
        """If -O and -D are provided, create new waypoints or merge them into existing waypoints."""
        origOffset, destOffset = self.offsetOrig, self.offsetDest
        if not flight.trackData or (origOffset is None and destOffset is None):
            return

        first = flight.trackData[0]
        last = flight.trackData[-1]
        firstLat = float(first["Latitude"])
        firstLon = float(first["Longitude"])
        lastLat = float(last["Latitude"])
        lastLon = float(last["Longitude"])
        flightMeta = flight.metaData

        if origOffset is not None and destOffset is not None:
            firstWaypoint = self._nearestWaypoint(firstLat, firstLon, waypoints)
            lastWaypoint = self._nearestWaypoint(lastLat, lastLon, waypoints)

            # If origin/destination resolve to the same location, collapse -O/-D into one averaged offset.
            if firstWaypoint is lastWaypoint and firstWaypoint is not None:
                offset = origOffset.averageWith(destOffset)
                firstWaypoint.offset = firstWaypoint.offset + offset
                self._warnConfig(f"CLI: -O and -D applied to the same inner zone ({firstWaypoint.code}). Offsets were averaged.")
                return

            firstLastDistanceNm = greatCircleDistanceNm(firstLat, firstLon, lastLat, lastLon)
            # If origin/destination are very close to each other, create a HOME waypoint at the midpoint.
            if firstLastDistanceNm <= self.OFFSET_INNER_RADIUS_NM + 1e-9:
                midLat = 0.5 * (firstLat + lastLat)
                midLon = 0.5 * (firstLon + lastLon)
                offset = origOffset.averageWith(destOffset)
                self._appendSyntheticCliWaypoint(waypoints, seenCodes, midLat, midLon, offset, "HOME")
                differNote = " -O and -D differ. Offsets were averaged." if not origOffset.approxEqual(destOffset) else ""
                self._warnConfig(
                    "CLI: first and last track points are within the default inner radius of each other. "
                    "Using midpoint position and averaged -O/-D offsets (HOME)."
                    + differNote
                )
                return

        if origOffset is not None:
            self._applyCliOffsetAtPosition(flightMeta, True, firstLat, firstLon, origOffset, waypoints, seenCodes)
        if destOffset is not None:
            self._applyCliOffsetAtPosition(flightMeta, False, lastLat, lastLon, destOffset, waypoints, seenCodes)


    def _resolveWaypoint(
        self,
        waypoint: "WaypointEntry",
        lookupMap: Dict[str, "OurAirportsRecord"],
    ) -> Optional["WaypointEntry"]:
        """Resolve missing coordinates for a configured waypoint location using coordinates from a matching OurAirports record."""
        if waypoint.hasCoordinates():
            return waypoint

        if not self.airfieldDbEnabled:
            return None

        record = lookupMap.get(waypoint.code.upper())
        if record is None:
            return None
        return WaypointEntry(
            code=waypoint.code,
            offset=waypoint.offset,
            innerRadiusNm=waypoint.innerRadiusNm,
            outerRadiusNm=waypoint.outerRadiusNm,
            visitRadiusNm=waypoint.visitRadiusNm,
            hideFromRoute=waypoint.hideFromRoute,
            lattitude=record.lattitude,
            longitude=record.longitude,
        )


    @staticmethod
    def _nearestWaypoint(
        latitude: float,
        longitude: float,
        waypoints: List["WaypointEntry"],
    ) -> Optional["WaypointEntry"]:
        """Waypoints whose inner disc contains (lat, lon), with smallest distance to center wins."""
        best: Optional[WaypointEntry] = None
        bestDistance = float("inf")
        for entry in waypoints:
            entryLat, entryLon = entry.lattitude, entry.longitude
            if entryLat is None or entryLon is None:
                continue
            distanceNm = greatCircleDistanceNm(latitude, longitude, entryLat, entryLon)
            if distanceNm > entry.innerRadiusNm + 1e-9:
                continue
            if distanceNm < bestDistance:
                bestDistance = distanceNm
                best = entry
        return best


    def _airfieldLookupMap(self, records: List["OurAirportsRecord"]) -> Dict[str, "OurAirportsRecord"]:
        lookup: Dict[str, OurAirportsRecord] = {}
        for record in records:
            for key in [record.ident, record.gpsCode, record.localCode, record.iataCode]:
                normalized = key.strip().upper()
                if normalized and normalized not in lookup:
                    lookup[normalized] = record
        return lookup


    def _filterAirfields(
        self,
        boundingBoxes: List["BoundingBox"],
        airfieldCategory: Optional[str] = None,
    ) -> List["OurAirportsRecord"]:
        """Filter OurAirports records by type and bounding box."""
        records = self._loadOurAirportsRecords()
        if not records:
            return []

        category = airfieldCategory or self.aircraftType
        allowedTypes = self.AIRFIELD_TYPES_BY_AIRCRAFT.get(
            category, self.AIRFIELD_TYPES_BY_AIRCRAFT[self.AIRCRAFT_TYPE_DEFAULT]
        )
        typed = [record for record in records if record.type in allowedTypes]

        if not boundingBoxes:
            return typed

        maxOuterRadius = max(
            [self.OFFSET_OUTER_RADIUS_NM]
            + [waypoint.outerRadiusNm for waypoint in self.waypoints]
        )
        return [
            record for record in typed
            if self._isInFlightBounds(record.lattitude, record.longitude, boundingBoxes, maxOuterRadius)
        ]


    @staticmethod
    def _isInFlightBounds(latitude: float, longitude: float, boundingBoxes: List["BoundingBox"], radiusNm: float) -> bool:
        if not boundingBoxes:
            return True
        for box in boundingBoxes:
            if box.contains(latitude, longitude, marginNm=radiusNm):
                return True
        return False


    def _applyAirfieldDbSection(self, cliAirfieldDb: Optional[str]) -> None:
        """Read ``[AirfieldDB]``: MaxAgeDays, Path, and per-type visit radii for route detection."""
        dbPathViaCli = cliAirfieldDb not in (None, '')
        if cliAirfieldDb is not None:
            self.airfieldDbEnabled = True
            self.airfieldDbPath = self._resolveAirfieldDbPath(cliAirfieldDb)

        if self.file and 'AirfieldDB' in self.file:
            section = self.file['AirfieldDB']
            if cliAirfieldDb is None:
                self.airfieldDbEnabled = self._parseEnableFlag(section, 'enabled')

            for key, raw in section.items():
                if key == 'enabled':
                    continue
                elif key == 'maxagedays':
                    self.airfieldDbMaxAgeDays = self._parseFloat(section, 'maxagedays')
                elif key == 'path':
                    if self.airfieldDbEnabled and not dbPathViaCli:
                        dbPath = '' if raw is None else str(raw).strip()
                        self.airfieldDbPath = self._resolveAirfieldDbPath(dbPath)
                elif key == 'defaultvisitradius' or key in self._AIRFIELDS_VISIT_RADIUS_OPTION_TO_TYPE:
                    try:
                        val = max(0.0, float(str(raw).strip()))
                    except ValueError:
                        raise ConfigError(f"Invalid {key!r} in [AirfieldDB]: {raw!r}.") from None

                    if key == 'defaultvisitradius':
                        self.airfieldDefaultVisitRadiusNm = val
                    else:
                        recType = self._AIRFIELDS_VISIT_RADIUS_OPTION_TO_TYPE[key]
                        self.airfieldTypeVisitRadiusNm[recType] = val
                else:
                    if not key.startswith('dref '):
                        self._warnConfig(f"Unknown key {key!r} in [AirfieldDB] (ignored).")

            if self.airfieldDbEnabled and self.airfieldDbPath is None:
                self.airfieldDbPath = self._resolveAirfieldDbPath('')


    def _loadOurAirportsRecords(self) -> List["OurAirportsRecord"]:
        """Load records from OurAirports database into memory."""
        if not self.airfieldDbEnabled or self.airfieldDbPath is None:
            return []
        if self._airfieldRecords is not None:
            return self._airfieldRecords

        dbPath = self.airfieldDbPath
        dbExists = dbPath.is_file()
        if not dbExists:
            self._infoAirfield(f"database not found at {dbPath}. Downloading OurAirports data.")
            try:
                self._downloadOurAirportsDb(dbPath)
            except Exception as err:
                raise ConfigError(
                    f"Airfield database could not be downloaded to {dbPath}: {err}"
                ) from err
            if not dbPath.is_file():
                raise ConfigError(
                    f"Airfield database download did not produce a file at {dbPath}."
                )

        if dbPath.is_file() and self._isAirfieldDbStale(dbPath):
            self._infoAirfield(
                f"database is older than {self.airfieldDbMaxAgeDays:g} days ({dbPath}). Attempting refresh."
            )
            try:
                self._downloadOurAirportsDb(dbPath)
            except Exception as err:
                self._infoAirfield(f"refresh failed ({err}). Continuing with existing file.")
        if not dbPath.is_file():
            raise ConfigError(f"Airfield database required but no usable file at {dbPath}.")

        self._airfieldRecords = self._readOurAirportsCsv(dbPath)
        return self._airfieldRecords


    def _downloadOurAirportsDb(self, dbPath: Path) -> None:
        dbPath.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.csv', dir=str(dbPath.parent)) as tmp:
            tempPath = Path(tmp.name)

        try:
            with urlrequest.urlopen(self.AIRFIELD_DB_URL, timeout=30) as response:
                payload = response.read()
            tempPath.write_bytes(payload)
            tempPath.replace(dbPath)
        except (OSError, urlerror.URLError) as err:
            if tempPath.exists():
                tempPath.unlink()
            raise RuntimeError(f"unable to download {self.AIRFIELD_DB_URL}: {err}") from err


    def _readOurAirportsCsv(self, dbPath: Path) -> List["OurAirportsRecord"]:
        records: List[OurAirportsRecord] = []
        with dbPath.open('r', encoding='utf-8', newline='') as dbFile:
            reader = csv.DictReader(dbFile)
            for row in reader:
                recordType = (row.get('type') or '').strip().lower()

                latRaw = row.get('latitude_deg', '')
                lonRaw = row.get('longitude_deg', '')
                try:
                    lattitude = float(latRaw)
                    longitude = float(lonRaw)
                except (TypeError, ValueError):
                    continue
                records.append(
                    OurAirportsRecord(
                        ident=(row.get('ident') or '').strip(),
                        gpsCode=(row.get('gps_code') or '').strip(),
                        localCode=(row.get('local_code') or '').strip(),
                        iataCode=(row.get('iata_code') or '').strip(),
                        name=(row.get('name') or '').strip(),
                        type=recordType,
                        lattitude=lattitude,
                        longitude=longitude,
                    )
                )
        return records


    def _isAirfieldDbStale(self, dbPath: Path) -> bool:
        try:
            mtime = dbPath.stat().st_mtime
        except OSError:
            return True
        ageDays = (datetime.now() - datetime.fromtimestamp(mtime)).total_seconds() / 86400.0
        return ageDays > self.airfieldDbMaxAgeDays


    def _resolveAirfieldDbPath(self, dbValue: str) -> Path:
        """Resolve the path to the airfield database. If empty, use the default filename in the current directory."""
        if not dbValue:
            return Path(os.path.dirname(os.path.abspath(__file__))) / self.AIRFIELD_DB_DEFAULT_FILENAME

        pathString = dbValue.strip()
        path = Path(pathString).expanduser()
        if pathString.endswith(('/', '\\')) or (path.exists() and path.is_dir()) or path.suffix == '':
            path = path / self.AIRFIELD_DB_DEFAULT_FILENAME
        return path


    def _loadWaypoints(self) -> List["WaypointEntry"]:
        entries: List["WaypointEntry"] = []
        if not self.file:
            return entries

        for section in self.file.sections():
            if not section.lower().startswith(self._WAYPOINT_SECTION_PREFIX):
                continue

            waypointName = section[len(self._WAYPOINT_SECTION_PREFIX):].strip()
            if not waypointName:
                raise ConfigError(f"[{section}] does not include a waypoint name after the prefix.")

            sectionData = self.file[section]
            hideFromRoute = self._parseEnableFlag(sectionData, 'hideFromRoute')
            offset = self._parseOffset(sectionData, 'offset', hideFromRoute)
            innerRadiusNm = self._parseFloat(sectionData, 'innerradiusnm', self.OFFSET_INNER_RADIUS_NM)
            outerRadiusNm = self._parseFloat(sectionData, 'outerradiusnm', self.OFFSET_OUTER_RADIUS_NM)

            lat = self._parseOptionalFloat(sectionData, 'lat')
            lon = self._parseOptionalFloat(sectionData, 'lon')
            if not self.airfieldDbEnabled and not hideFromRoute and (lat is None or lon is None):
                raise ConfigError(
                    f"[{section}] requires both lat and lon when airfield DB lookup is not enabled."
                )

            entries.append(
                WaypointEntry(
                    code=waypointName,
                    offset=offset,
                    innerRadiusNm=innerRadiusNm,
                    outerRadiusNm=outerRadiusNm,
                    hideFromRoute=hideFromRoute,
                    lattitude=lat,
                    longitude=lon,
                )
            )

        return entries


    @staticmethod
    def _parseFloat(
        section: Any,
        key: str,
        default: Optional[float] = None,
    ) -> float:
        """Parse a float in the form '1.23' with optional sign. If missing, return the default."""
        label = f"[{getattr(section, 'name', 'Defaults')}]"
        if not section or key not in section:
            if default is not None:
                return default
            raise ConfigError(f"Missing {key!r} in {label}.")

        rawValue = section[key]
        try:
            return float(rawValue)
        except ValueError:
            raise ConfigError(f"Invalid {key} value in {label}: {rawValue!r}.")


    @staticmethod
    def _parseOptionalFloat(section: Any, key: str) -> Optional[float]:
        """Parse a float in the form '1.23' with optional sign."""
        if not section or key not in section:
            return None
        return Config._parseFloat(section, key)


    @staticmethod
    def _parseTimezone(section: Any, key: str) -> float:
        """Parse a timezone offset in the form '-5', '5.5', or '+05:30'."""
        label = f"[{getattr(section, 'name', 'Defaults')}]"
        if not section or key not in section:
            raise ConfigError(f"Missing {key!r} in {label}.")

        rawValue = section[key]
        try:
            return timezoneOffsetInSeconds(rawValue)
        except (ValueError, IndexError):
            raise ConfigError(
                f"{key} in {label} must be a timezone offset like '-5', '5.5', or '+05:30'. Got {rawValue!r}."
            )


    @staticmethod
    def _parseOffset(section: Any, key: str, hideFromRoute: bool) -> Optional[CardinalOffset]:
        """Parse an offset in the form 'east,north,up' with optional sign on each (all feet)."""
        label = f"[{getattr(section, 'name', 'Defaults')}]"
        offsetRaw = section.get(key)
        if offsetRaw is None:
            if not hideFromRoute:
                raise ConfigError(
                    f"{label} requires {key} unless hideFromRoute = true."
                )
            return None

        try:
            return CardinalOffset.fromString(offsetRaw)
        except ValueError as err:
            raise ConfigError(f"{label} has invalid {key}: {err}") from None


    @staticmethod
    def _parseEnableFlag(section: Any, key: str) -> bool:
        """Parse a boolean in the form True/Yes/1/On or False/No/0/Off. A bare key with no value is true."""
        normalKey = key.lower()
        if not section or normalKey not in section:
            return False
        rawValue = section.get(normalKey)
        if rawValue is None:
            return True
        normalValue = rawValue.strip().lower()
        if not normalValue:
            return True
        if normalValue in {"true", "yes", "1", "on"}:
            return True
        if normalValue in {"false", "no", "0", "off"}:
            return False

        label = f"[{getattr(section, 'name', 'Defaults')}]"
        raise ConfigError(f"Invalid {key} value in {label}: {rawValue!r}.")


    def _findConfigFile(self, cliPath:str):
        if cliPath:
            return os.path.expanduser(cliPath)
        
        paths = ('.', os.path.dirname(os.path.abspath(__file__)))
        files = ('42fdr.conf', '42fdr.ini')
        for path in paths:
            for file in files:
                fullPath = os.path.join(path, file)
                if Path(fullPath).is_file():
                    return fullPath

        return None


    @staticmethod
    def _warnConfig(message: str) -> None:
        print(f"Config warning: {message}", file=sys.stderr)


    @staticmethod
    def _infoAirfield(message: str) -> None:
        print(f"Airfield data: {message}", file=sys.stderr)


class FlightMeta():
    Pilot                  : Optional[str]       = None
    TailNumber             : Optional[str]       = None
    DerivedOrigin          : Optional[str]       = None
    StartLatitude          : Optional[float]     = None
    StartLongitude         : Optional[float]     = None
    DerivedDestination     : Optional[str]       = None
    EndLatitude            : Optional[float]     = None
    EndLongitude           : Optional[float]     = None
    StartTime              : Optional[datetime]  = None
    EndTime                : Optional[datetime]  = None
    TotalDuration          : Optional[timedelta] = None
    TotalDistance          : Optional[float]     = None
    InitialAttitudeSource  : Optional[str]       = None
    DeviceModel            : Optional[str]       = None
    DeviceDetails          : Optional[str]       = None
    DeviceVersion          : Optional[str]       = None
    BatteryLevel           : Optional[float]     = None
    BatteryState           : Optional[str]       = None
    GPSSource              : Optional[str]       = None
    MaximumVerticalError   : Optional[float]     = None
    MinimumVerticalError   : Optional[float]     = None
    AverageVerticalError   : Optional[float]     = None
    MaximumHorizontalError : Optional[float]     = None
    MinimumHorizontalError : Optional[float]     = None
    AverageHorizontalError : Optional[float]     = None
    ImportedFrom           : Optional[str]       = None
    RouteWaypoints         : Optional[str]       = None
    DerivedRoute           : Optional[List[str]] = None


class BoundingBox:
    minLat: float
    maxLat: float
    minLon: float
    maxLon: float

    def __init__(self, latitude: float, longitude: float):
        self.minLat = latitude
        self.maxLat = latitude
        self.minLon = longitude
        self.maxLon = longitude

    def include(self, latitude: float, longitude: float) -> None:
        self.minLat = min(self.minLat, latitude)
        self.maxLat = max(self.maxLat, latitude)
        self.minLon = min(self.minLon, longitude)
        self.maxLon = max(self.maxLon, longitude)

    def contains(self, latitude: float, longitude: float, marginNm: float = 0.0) -> bool:
        latMargin = marginNm / 60.0
        lonMargin = longitudeDegreesForNm(marginNm, latitude)
        return (
            self.minLat - latMargin <= latitude <= self.maxLat + latMargin
            and self.minLon - lonMargin <= longitude <= self.maxLon + lonMargin
        )


class WaypointEntry:
    code: str
    offset: Optional[CardinalOffset]
    # innerRadiusNm / outerRadiusNm control the position-offset ramp used by
    # WaypointOffsetHelper: full offset inside inner, tapering to zero at outer.
    innerRadiusNm: float
    outerRadiusNm: float
    # visitRadiusNm controls route detection (how close the aircraft must get
    # before this waypoint is considered "visited"). Defaults to the inner
    # radius when not given explicitly.
    visitRadiusNm: float
    hideFromRoute: bool
    lattitude: Optional[float]
    longitude: Optional[float]

    def __init__(
        self,
        code: str,
        offset: Optional[CardinalOffset],
        innerRadiusNm: float,
        outerRadiusNm: float,
        visitRadiusNm: Optional[float] = None,
        hideFromRoute: bool = False,
        lattitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ):
        self.code = code
        self.offset = offset
        self.innerRadiusNm = max(0.0, innerRadiusNm)
        self.outerRadiusNm = max(self.innerRadiusNm, outerRadiusNm)
        self.visitRadiusNm = max(0.0, visitRadiusNm if visitRadiusNm is not None else self.innerRadiusNm)
        self.hideFromRoute = hideFromRoute
        self.lattitude = lattitude
        self.longitude = longitude


    def hasCoordinates(self) -> bool:
        return self.lattitude is not None and self.longitude is not None


class WaypointOffsetHelper:
    _waypoints: List[WaypointEntry]


    def __init__(self):
        self._waypoints = []


    def addWaypoint(
        self,
        code: str,
        lattitude: float,
        longitude: float,
        offset: CardinalOffset,
        innerRadiusNm: float,
        outerRadiusNm: float,
    ) -> None:
        self._waypoints.append(
            WaypointEntry(
                code=code,
                offset=offset,
                innerRadiusNm=innerRadiusNm,
                outerRadiusNm=outerRadiusNm,
                lattitude=lattitude,
                longitude=longitude,
            )
        )

    def offsetForPosition(self, lattitude: float, longitude: float) -> Optional[GeodeticOffset]:
        cardinalOffset = self._offsetFeetForPosition(lattitude, longitude)
        if cardinalOffset is None:
            return None
        return self._cardinalToGeodeticOffset(cardinalOffset, lattitude)


    def _offsetFeetForPosition(self, lattitude: float, longitude: float) -> Optional[CardinalOffset]:
        innerMatches: List[Tuple[float, WaypointEntry]] = []
        outerMatches: List[Tuple[float, WaypointEntry]] = []

        for entry in self._waypoints:
            if entry.lattitude is not None and entry.longitude is not None and entry.offset is not None:
                distanceNm = greatCircleDistanceNm(lattitude, longitude, entry.lattitude, entry.longitude)
                if distanceNm <= entry.innerRadiusNm:
                    innerMatches.append((distanceNm, entry))
                elif distanceNm <= entry.outerRadiusNm:
                    outerMatches.append((distanceNm, entry))

        if innerMatches:
            centerDistances = [distanceNm for distanceNm, _ in innerMatches]
            blendWeights = self._inverseRatioWeights(centerDistances)
            totalWeight = 0.0
            eastSum = 0.0
            northSum = 0.0
            upSum = 0.0
            for weight, (_, entry) in zip(blendWeights, innerMatches):
                if entry.offset is not None:
                    totalWeight += weight
                    eastSum += entry.offset.eastFt * weight
                    northSum += entry.offset.northFt * weight
                    upSum += entry.offset.upFt * weight
            if totalWeight > 0:
                return CardinalOffset(
                    eastFt=eastSum / totalWeight,
                    northFt=northSum / totalWeight,
                    upFt=upSum / totalWeight,
                )
            return None

        if outerMatches:
            innerEdgeDistances: List[float] = []
            localOffsets: List[CardinalOffset] = []
            for distanceNm, entry in outerMatches:
                if entry.offset is None:
                    continue
                ringWidthNm = entry.outerRadiusNm - entry.innerRadiusNm
                if ringWidthNm > 0:
                    localWeight = (entry.outerRadiusNm - distanceNm) / ringWidthNm
                    localWeight = max(0.0, min(1.0, localWeight))
                    if localWeight > 0:
                        innerEdgeDistances.append(max(0.0, distanceNm - entry.innerRadiusNm))
                        localOffsets.append(
                            CardinalOffset(
                                eastFt=entry.offset.eastFt * localWeight,
                                northFt=entry.offset.northFt * localWeight,
                                upFt=entry.offset.upFt * localWeight,
                            )
                        )

            if not localOffsets:
                return None

            blendWeights = self._inverseRatioWeights(innerEdgeDistances)
            totalWeight = 0.0
            eastSum = 0.0
            northSum = 0.0
            upSum = 0.0
            for weight, localOffset in zip(blendWeights, localOffsets):
                totalWeight += weight
                eastSum += localOffset.eastFt * weight
                northSum += localOffset.northFt * weight
                upSum += localOffset.upFt * weight
            if totalWeight > 0:
                return CardinalOffset(
                    eastFt=eastSum / totalWeight,
                    northFt=northSum / totalWeight,
                    upFt=upSum / totalWeight,
                )

        return None


    @staticmethod
    def _inverseRatioWeights(distances: List[float]) -> List[float]:
        """Blend waypoint offsets by closeness."""
        if not distances:
            return []
        epsilon = 1e-12
        zeroDistanceIndexes = [i for i, distance in enumerate(distances) if distance <= epsilon]
        if zeroDistanceIndexes:
            # Avoid division by zero, handle edge case of multiple waypoints at exact center.
            dominantWeight = 1.0 / len(zeroDistanceIndexes)
            return [dominantWeight if i in zeroDistanceIndexes else 0.0 for i in range(len(distances))]

        longestDistance = max(distances)
        if longestDistance <= epsilon:
            uniformWeight = 1.0 / len(distances)
            return [uniformWeight for _ in distances]

        rawWeights = [longestDistance / distance for distance in distances]
        totalRawWeight = sum(rawWeights)
        if totalRawWeight <= epsilon:
            uniformWeight = 1.0 / len(distances)
            return [uniformWeight for _ in distances]
        return [weight / totalRawWeight for weight in rawWeights]


    @staticmethod
    def _cardinalToGeodeticOffset(cardinalOffset: CardinalOffset, startLatttitude: float) -> GeodeticOffset:
        earthRadiusFeet = 20925524.9
        deltaLat = math.degrees(cardinalOffset.northFt / earthRadiusFeet)
        cosLat = math.cos(math.radians(startLatttitude))
        if abs(cosLat) < 1e-12:
            deltaLong = 0.0
        else:
            deltaLong = math.degrees(cardinalOffset.eastFt / (earthRadiusFeet * cosLat))
        return GeodeticOffset(
            deltaLatitude=deltaLat,
            deltaLongitude=deltaLong,
            deltaAltitude=cardinalOffset.upFt,
        )


class OurAirportsRecord:
    ident: str
    gpsCode: str
    localCode: str
    iataCode: str
    name: str
    type: str
    lattitude: float
    longitude: float

    def __init__(
        self,
        ident: str,
        gpsCode: str,
        localCode: str,
        iataCode: str,
        name: str,
        type: str,
        lattitude: float,
        longitude: float,
    ):
        self.ident = ident
        self.gpsCode = gpsCode
        self.localCode = localCode
        self.iataCode = iataCode
        self.name = name
        self.type = type
        self.lattitude = lattitude
        self.longitude = longitude


class FdrTrackPoint():
    TIME:datetime
    LAT:float
    LONG:float
    ALTMSL:float
    HEADING:float
    PITCH:float
    ROLL:float
    offset: Optional[GeodeticOffset]

    drefs: Dict[str, float]


    def __init__(self, time:datetime, latitude:float, longitude:float, altitude:float, heading:float, pitch:float, roll:float):
        self.TIME = time
        self.LAT = latitude
        self.LONG = longitude
        self.ALTMSL = altitude
        self.HEADING = heading
        self.PITCH = pitch
        self.ROLL = roll
        self.offset = None
        self.drefs = {}


    def addOffset(self, offset: GeodeticOffset) -> None:
        self.offset = offset


    def renderPosition(self) -> Tuple[float, float, float]:
        if self.offset is None:
            return (self.LAT, self.LONG, self.ALTMSL)
        return (
            self.LAT + self.offset.deltaLatitude,
            self.LONG + self.offset.deltaLongitude,
            self.ALTMSL + self.offset.deltaAltitude,
        )


    def addDrefs(
        self,
        drefSources: Dict[str, str],
        flightMeta: FlightMeta,
        trackData: dict[str, Any],
    ) -> None:
        meta = vars(flightMeta)
        point = vars(self)
        for name, expr in drefSources.items():
            self.drefs[name] = eval(expr.format(**meta, **point, **trackData))


class FdrFlight():
    ACFT:str = ''
    TAIL:str = ''
    DATE:date = datetime.today()
    PRES:float = 0
    DISA:int = 0
    WIND:Tuple[int, int] = (0,0)

    timezone:float = 0
    track:List[FdrTrackPoint]
    metaData: Optional[FlightMeta] = None
    trackData: List[Dict[str, Any]]


    def __init__(self):
        self.track = []
        self.trackData = []
        self.metaData = None


    def _buildBoundingBoxes(self, cellSizeNm: float) -> List[BoundingBox]:
        # Bucket track points into grid-local boxes to optimize nearby-airfield checks.
        if not self.trackData:
            return []
        first = self.trackData[0]
        originLat = float(first['Latitude'])
        originLon = float(first['Longitude'])
        if cellSizeNm <= 0:
            box = BoundingBox(originLat, originLon)
            for trackData in self.trackData[1:]:
                box.include(float(trackData['Latitude']), float(trackData['Longitude']))
            return [box]

        boxesByCell: Dict[Tuple[int, int], BoundingBox] = {}
        for trackData in self.trackData:
            lat = float(trackData['Latitude'])
            lon = float(trackData['Longitude'])
            northNm = latitudeDegreesToNm(lat - originLat)
            eastNm = longitudeDegreesToNm(lon - originLon, (lat + originLat) / 2.0)
            cellX = math.floor(eastNm / cellSizeNm)
            cellY = math.floor(northNm / cellSizeNm)
            key = (cellX, cellY)
            if key not in boxesByCell:
                boxesByCell[key] = BoundingBox(lat, lon)
            else:
                boxesByCell[key].include(lat, lon)

        return list(boxesByCell.values())


    def buildTrackPoints(self, config: Config) -> None:
        meta = self.metaData or FlightMeta()
        tailConfig = config.tailConfigFor(self.TAIL)
        drefSources, _ = config.drefsByTail(self.TAIL)
        boundingBoxes = self._buildBoundingBoxes(config.airfieldGridCellNm)
        waypoints = config.waypointsForFlight(self, boundingBoxes)
        offsetHelper = config.offsetHelperFrom(waypoints)

        visibleWaypoints: Optional[List[WaypointEntry]]
        if config.enableRouting:
            visibleWaypoints = [
                w for w in waypoints if not w.hideFromRoute and w.hasCoordinates()
            ]
        else:
            visibleWaypoints = None

        derivedRoute: List[str] = []
        lastCode: Optional[str] = None
        nearestCode: Optional[str] = None
        hasDeparted: bool = False

        for trackData in self.trackData:
            point = FdrTrackPoint(
                time      = datetime.fromtimestamp(float(trackData['Timestamp']) + self.timezone),
                latitude  = float(trackData['Latitude']),
                longitude = float(trackData['Longitude']),
                altitude  = float(trackData['Altitude']),
                heading   = wrapHeading(float(trackData['Course']) + tailConfig['headingtrim']),
                pitch     = wrapAttitude(float(trackData['Pitch']) + tailConfig['pitchtrim']),
                roll      = wrapAttitude(float(trackData['Bank']) + tailConfig['rolltrim'])
            )
            point.addDrefs(drefSources, meta, trackData)

            offset = offsetHelper.offsetForPosition(point.LAT, point.LONG)
            if offset is not None:
                point.addOffset(offset)

            self.track.append(point)

            if visibleWaypoints is not None:
                nearestCode = self._nearestWaypointCode(point.LAT, point.LONG, visibleWaypoints)
                if nearestCode != lastCode:
                    if lastCode is not None:
                        hasDeparted = True
                    if nearestCode is not None:
                        lastCode = nearestCode
                        derivedRoute.append(nearestCode)

        if len(derivedRoute) == 1 and hasDeparted:
            if nearestCode is not None and nearestCode == derivedRoute[0]:
                # Make sure round trips with only one waypoint are expanded to two.
                derivedRoute.append(nearestCode)

        meta.DerivedRoute = derivedRoute if config.enableRouting else None


    @staticmethod
    def _nearestWaypointCode(
        lattitude: float,
        longitude: float,
        waypoints: List[WaypointEntry],
    ) -> Optional[str]:
        nearestCode: Optional[str] = None
        nearestDistance = float('inf')
        for waypoint in waypoints:
            assert waypoint.lattitude is not None and waypoint.longitude is not None
            distance = greatCircleDistanceNm(
                lattitude, longitude, waypoint.lattitude, waypoint.longitude
            )
            if distance <= waypoint.visitRadiusNm:
                if distance < nearestDistance:
                    nearestDistance = distance
                    nearestCode = waypoint.code
        return nearestCode


    def deriveMissingMetaData(self) -> None:
        """Backfill FlightMeta fields that weren't available during format-specific parsing."""
        meta = self.metaData
        if not meta:
            return

        if not self.track:
            return
        firstPoint = self.track[0]
        lastPoint = self.track[-1]

        if meta.StartTime is None:
            meta.StartTime = firstPoint.TIME
        if meta.StartLatitude is None:
            meta.StartLatitude = firstPoint.LAT
        if meta.StartLongitude is None:
            meta.StartLongitude = firstPoint.LONG
        if meta.EndTime is None:
            meta.EndTime = lastPoint.TIME
        if meta.EndLatitude is None:
            meta.EndLatitude = lastPoint.LAT
        if meta.EndLongitude is None:
            meta.EndLongitude = lastPoint.LONG
        if meta.TotalDuration is None and meta.StartTime and meta.EndTime:
            meta.TotalDuration = meta.EndTime - meta.StartTime
        if self.DATE == datetime.today().date() and meta.StartTime:
            self.DATE = meta.StartTime.date()


    def summary(self) -> str:
        flightMeta = self.metaData or FlightMeta()

        pilot        = f' by {flightMeta.Pilot}' if flightMeta.Pilot else ''
        distance     = f" {flightMeta.TotalDistance:.2f} miles" if flightMeta.TotalDistance else ""
        hoursMinutes = str(flightMeta.TotalDuration).split(':')[:2]
        origin       = flightMeta.DerivedOrigin or "N/A"
        destination  = flightMeta.DerivedDestination or "N/A"
        plannedRoute = flightMeta.RouteWaypoints or "N/A"
        derivedLine = ""
        if flightMeta.DerivedRoute is not None:
            derivedRoute = " ".join(flightMeta.DerivedRoute) if flightMeta.DerivedRoute else "N/A"
            derivedLine = f"\n Derived: {derivedRoute}"

        startTime = flightMeta.StartTime
        endTime   = flightMeta.EndTime
        ymd       = toYMD(startTime) if startTime is not None else "N/A"
        startHM   = toHM(startTime) if startTime is not None else "--:--"
        endHM     = toHM(endTime) if endTime is not None else "--:--"
        startLat  = str(self._roundLatLong(float(flightMeta.StartLatitude))) if flightMeta.StartLatitude is not None else "N/A"
        startLong = str(self._roundLatLong(float(flightMeta.StartLongitude))) if flightMeta.StartLongitude is not None else "N/A"
        endLat    = str(self._roundLatLong(float(flightMeta.EndLatitude))) if flightMeta.EndLatitude is not None else "N/A"
        endLong   = str(self._roundLatLong(float(flightMeta.EndLongitude))) if flightMeta.EndLongitude is not None else "N/A"

        clientLine = ''
        deviceInfo = flightMeta.DeviceDetails or flightMeta.DeviceModel
        if deviceInfo:
            clientLine = f"\n  Client: {deviceInfo}"
            if flightMeta.DeviceVersion:
                clientLine += f" iOS v{flightMeta.DeviceVersion}"

        importedLine = ''
        if flightMeta.ImportedFrom and flightMeta.ImportedFrom != 'iOS':
            importedLine = f"\nImported: {flightMeta.ImportedFrom}"

        heading = f"{flightMeta.TailNumber} - {ymd}{distance}{pilot} ({hoursMinutes[0]} hours and {hoursMinutes[1]} minutes)"
        underline = '\n'+ ('-' * len(heading))

        return f'''{heading}{underline}
    From: {startHM}Z {origin} ({startLat}, {startLong})
      To: {endHM}Z {destination} ({endLat}, {endLong})
 Planned: {plannedRoute}{derivedLine}
GPS/AHRS: {flightMeta.GPSSource}''' + clientLine + importedLine


    @staticmethod
    def _roundLatLong(value: float) -> float:
        return round(value, 9)

    @staticmethod
    def _roundAltitude(value: float) -> float:
        return round(value, 4)

    @staticmethod
    def _roundAttitude(value: float) -> float:
        return round(value, 3)

    @staticmethod
    def _roundHeading(value: float) -> float:
        return round(value, 3)


    @staticmethod
    def _fdrComment(comment: str) -> str:
        return 'COMM, '+ '\nCOMM, '.join(comment.splitlines()) +'\n'


    @staticmethod
    def _fdrDrefs(drefDefines: List[str]) -> str:
        return 'DREF, ' + '\nDREF, '.join(drefDefines) +'\n'


    @staticmethod
    def _fdrColNames(drefNames: Iterable[str]) -> str:
        names = '''COMM,                        degrees,             degrees,              ft msl,                 deg,                 deg,                 deg
COMM,                      Longitude,            Latitude,              AltMSL,             Heading,               Pitch,                Roll'''

        for drefName in drefNames:
            names += ', '+ str.rjust(drefName, FdrColumnWidth)

        return names +'\n'


    def writeFdrFile(self, config: Config, fdrFile: TextIO) -> None:
        timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M:%SZ')
        drefSources, drefDefines = config.drefsByTail(self.TAIL)

        tzOffset = self.timezone
        if tzOffset:
            totalMinutes = abs(int(tzOffset)) // 60
            hours, minutes = divmod(totalMinutes, 60)
            direction = "added to" if tzOffset > 0 else "subtracted from"
            parts = []
            if hours:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes:
                parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            tzComment = " and ".join(parts)
            tzOffsetExplanation = f"All timestamps below this line have had {tzComment} {direction} their original values."
        else:
            tzOffsetExplanation = "All timestamps below this line are in the same timezone as the original file."

        fdrFile.writelines([
            'A\n4\n',
            '\n',
            self._fdrComment(f'Generated on [{timestamp}]'),
            self._fdrComment(f'This X-Plane compatible FDR file was converted from a ForeFlight track file using 42fdr.py'),
            self._fdrComment('https://github.com/MadReasonable/42fdr'),
            '\n',
            self._fdrComment(tzOffsetExplanation),
            '\n',
            self._fdrComment(self.summary()),
            '\n\n',
            self._fdrComment("Fields below define general data for this flight."),
            self._fdrComment("ForeFlight only provides a few of the data points that X-Plane can accept.") ,
            '\n',
            f'ACFT, {config.aircraftPathForTail(self.TAIL)}\n',
            f'TAIL, {self.TAIL}\n',
            f'DATE, {toMDY(self.DATE)}\n',
            '\n\n',
            self._fdrComment('DREFs below (if any) define additional columns beyond the 7th (Roll)'),
            self._fdrComment('in the flight track data that follows.'),
            '\n',
            self._fdrDrefs(drefDefines),
            '\n\n',
            self._fdrComment('The remainder of this file consists of GPS/AHRS track points.'),
            '\n',
            self._fdrColNames(drefSources.keys()),
        ])

        for point in self.track:
            outLat, outLong, outAltMSL = point.renderPosition()
            # Keep hundredths only. More precision triggers X-Plane "Out of range FDR-file time!".
            time    = point.TIME.strftime('%H:%M:%S.%f')[:-4]
            long    = str.rjust(str(self._roundLatLong(outLong)), FdrColumnWidth)
            lat     = str.rjust(str(self._roundLatLong(outLat)), FdrColumnWidth)
            altMSL  = str.rjust(str(self._roundAltitude(outAltMSL)), FdrColumnWidth)
            heading = str.rjust(str(self._roundHeading(point.HEADING)), FdrColumnWidth)
            pitch   = str.rjust(str(self._roundAttitude(point.PITCH)), FdrColumnWidth)
            roll    = str.rjust(str(self._roundAttitude(point.ROLL)), FdrColumnWidth)
            fdrFile.write(f'{time}, {long}, {lat}, {altMSL}, {heading}, {pitch}, {roll}')

            drefValues = []
            for dref in drefSources:
                drefValues.append(str.rjust(str(point.drefs[dref]), FdrColumnWidth))
            fdrFile.write(', '+ ', '.join(drefValues) +'\n')


class _ArgparseHelpFormatter(argparse.HelpFormatter):
    """Increase first column width so long option names fit on one line."""
    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=50)


def _buildArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Convert ForeFlight compatible track files into X-Plane compatible FDR files',
        epilog='Example: python 42fdr.py tracklog-1.csv tracklog-2.kml',
        formatter_class=_ArgparseHelpFormatter,
    )

    parser.add_argument('-c', '--config',       default=None,  metavar='PATH',                                    help='Path to 42fdr config file')
    parser.add_argument('-a', '--aircraft',     default=None,                                                     help='Path to default X-Plane aircraft')
    parser.add_argument('-t', '--aircraftType', default=None,  metavar='TYPE', type=str.lower, choices=('airplane', 'helicopter', 'balloon'), help="Aircraft category driving airfield filtering. Default: airplane.")
    parser.add_argument('-z', '--timezone',     default=None,                                                     help='An offset to add to all times processed.  +/-hh:mm[:ss] or +/-<decimal hours>')
    parser.add_argument('-o', '--outputFolder', default=None,  metavar='PATH',                                    help='Path to write X-Plane compatible FDR v4 output file')
    parser.add_argument(      '--airfieldDB',   default=None,  dest='airfieldDB', action='store_const', const='', help='Enable local airfield lookup using OurAirports data (default OurAirports.csv path).')
    parser.add_argument(      '--airfieldDBPath',              dest='airfieldDB', metavar='PATH',                 help='Enable local airfield lookup using OurAirports data from a specific CSV file or directory.')
    parser.add_argument(      '--inferRoute',   default=False, action='store_true',                               help='Infer and include derived route metadata from visited waypoints.')

    parser.add_argument('-O', '--offsetOrig', default=None, dest='offsetOrig', metavar='EAST,NORTH,UP',
        help='Offset in feet (east, north, up) at track origin. Added to offset derived from config or OurAirports.'
    )
    parser.add_argument('-D', '--offsetDest', default=None, dest='offsetDest', metavar='EAST,NORTH,UP',
        help='Offset in feet (east, north, up) at track destination. Same as -O but for the last track point.'
    )

    parser.add_argument('trackfile', default=None, nargs='+', help='Path to one or more ForeFlight compatible track files (CSV, KML)')
    return parser


def main(argv:List[str]):
    parser = _buildArgParser()
    args = parser.parse_args(argv[1:])
    
    config = Config(args)
    hadAircraftConfigErrors = False
    hadFileNotFoundErrors = False
    hadInvalidInputErrors = False
    hadUnexpectedErrors = False
    for inPath in args.trackfile:
        print(f"{inPath} ->", end="")
        try:
            inPath = os.path.expanduser(inPath)
            trackFile = open(inPath, 'r')
            fdrFlight = parseTrackFile(config, trackFile)

            if fdrFlight is not None:
                fdrFlight.buildTrackPoints(config)
                fdrFlight.deriveMissingMetaData()
                outPath = getOutpath(config, inPath, fdrFlight)
                with open(outPath, 'w') as fdrFile:
                    fdrFlight.writeFdrFile(config, fdrFile)
                outputFilename = os.path.basename(outPath)
                print(f" {outputFilename}")
            else:
                print(f" No flight data found in {inPath}")
        except FileNotFoundError as e:
            hadFileNotFoundErrors = True
            print(f" [Error] File not found: {e.filename}")
        except AircraftConfigError as e:
            hadAircraftConfigErrors = True
            print(f" [Error] Invalid aircraft configuration: {e}")
        except ValueError as e:
            hadInvalidInputErrors = True
            print(f" [Error] Invalid input: {e}")
        except Exception as e:
            hadUnexpectedErrors = True
            print(f" [Unexpected Error] {e}")

    if hadUnexpectedErrors:
        return 1
    if hadFileNotFoundErrors:
        return 4
    if hadAircraftConfigErrors:
        return 3
    if hadInvalidInputErrors:
        return 5
    return 0


def getOutpath(config:Config, inPath:str, fdrFlight:FdrFlight):
    filename = os.path.basename(inPath)
    outPath = config.outPath or '.'
    return Path(os.path.join(outPath, filename)).with_suffix('.fdr')


def parseTrackFile(config:Config, trackFile:TextIO) -> Optional[FdrFlight]:
    try:
        filetype = detectFileType(trackFile)

        if filetype == FileType.CSV:
            return parseCsvFile(config, trackFile)
        if filetype == FileType.KML:
            return parseKmlFile(config, trackFile)
        if filetype == FileType.GPX:
            return parseGpxFile(config, trackFile)

        return None
    finally:
        if not trackFile.closed:
            trackFile.close()


def detectFileType(file:TextIO) -> FileType:
    filetype = FileType.UNKNOWN
    startingPos = file.tell()

    line = file.readline()
    if not line.startswith('<?xml'):
        filetype = FileType.CSV
    else:
        line = file.readline()
        if line.startswith('<kml'):
            filetype = FileType.KML
        elif line.startswith('<gpx'):
            filetype = FileType.GPX

    file.seek(startingPos)
    return filetype


def parseCsvFile(config:Config, trackFile:TextIO) -> FdrFlight:
    flightMeta = FlightMeta()
    fdrFlight = FdrFlight()
    fdrFlight.timezone = config.timezoneCSV if config.timezoneCSV is not None else config.timezone

    # Create a CSV reader
    csvReader = csv.reader(trackFile, delimiter=',', quotechar='"')

    # Read the metadata header row
    metaCols = readCsvRow(csvReader)
    if metaCols is None:
        raise ValueError('CSV file is missing the metadata header row')
    metaCols.remove('Battery State') # ForeFlight exports this without a matching value

    # Read the metadata values row
    metaVals = readCsvRow(csvReader)
    if metaVals is None:
        raise ValueError('CSV file is missing the metadata values row')

    # Populate flight metadata
    metaData = dict(zip(metaCols, metaVals))
    for colName in metaData:
        colValue = metaData[colName]
        if colName == 'Tail Number':
            flightMeta.TailNumber = colValue
            fdrFlight.TAIL = colValue
        elif colName == 'Derived Origin':
            flightMeta.DerivedOrigin = colValue
        elif colName == 'Start Latitude':
            flightMeta.StartLatitude = round(float(colValue), 7)
        elif colName == 'Start Longitude':
            flightMeta.StartLongitude = round(float(colValue), 7)
        elif colName == 'Derived Destination':
            flightMeta.DerivedDestination = colValue
        elif colName == 'End Latitude':
            flightMeta.EndLatitude = round(float(colValue), 7)
        elif colName == 'End Longitude':
            flightMeta.EndLongitude = round(float(colValue), 7)
        elif colName == 'Start Time':
            flightMeta.StartTime = datetime.fromtimestamp(float(colValue) / 1000 + fdrFlight.timezone)
            fdrFlight.DATE = flightMeta.StartTime.date()
        elif colName == 'End Time':
            flightMeta.EndTime = datetime.fromtimestamp(float(colValue) / 1000 + fdrFlight.timezone)
        elif colName == 'Total Duration':
            flightMeta.TotalDuration = timedelta(seconds=float(colValue))
        elif colName == 'Total Distance':
            flightMeta.TotalDistance = float(colValue)
        elif colName == 'Initial Attitude Source':
            flightMeta.InitialAttitudeSource = colValue
        elif colName == 'Device Model':
            flightMeta.DeviceModel = colValue
        elif colName == 'Device Model Detailed':
            flightMeta.DeviceDetails = colValue
        elif colName == 'iOS Version':
            flightMeta.DeviceVersion = colValue
        elif colName == 'Battery Level':
            flightMeta.BatteryLevel = float(colValue)
        elif colName == 'Battery State':
            flightMeta.BatteryState = colValue
        elif colName == 'GPS Source':
            flightMeta.GPSSource = colValue
        elif colName == 'Maximum Vertical Error':
            flightMeta.MaximumVerticalError = float(colValue)
        elif colName == 'Minimum Vertical Error':
            flightMeta.MinimumVerticalError = float(colValue)
        elif colName == 'Average Vertical Error':
            flightMeta.AverageVerticalError = float(colValue)
        elif colName == 'Maximum Horizontal Error':
            flightMeta.MaximumHorizontalError = float(colValue)
        elif colName == 'Minimum Horizontal Error':
            flightMeta.MinimumHorizontalError = float(colValue)
        elif colName == 'Average Horizontal Error':
            flightMeta.AverageHorizontalError = float(colValue)
        elif colName == 'Imported From':
            flightMeta.ImportedFrom = colValue
        elif colName == 'Route Waypoints':
            flightMeta.RouteWaypoints = colValue

    fdrFlight.metaData = flightMeta

    # Read the track header row
    trackCols = readCsvRow(csvReader)
    if trackCols is None:
        raise ValueError('CSV track header row is missing')

    # Read the track values rows
    trackVals = readCsvRow(csvReader)
    while trackVals:
        fdrFlight.trackData.append(dict(zip(trackCols, trackVals)))
        trackVals = readCsvRow(csvReader)

    # Return the flight data
    return fdrFlight


def readCsvRow(csvFile) -> Optional[List[str]]:
    try:
        return next(csvFile)
    except StopIteration:
        return None


def parseKmlFile(config: Config, trackFile: TextIO) -> FdrFlight:
    def normalizeTimestamp(raw: str) -> datetime:
        normalized = raw.strip().replace("Z", "+00:00")
        # Python accepts up to 6 fractional digits. Some ForeFlight exports include nanoseconds.
        withFractionMatch = re.match(r"^(.*?)(\.\d+)([+-]\d{2}:\d{2})$", normalized)
        if withFractionMatch is not None:
            prefix, fraction, tzSuffix = withFractionMatch.groups()
            if len(fraction) > 7:
                normalized = f"{prefix}{fraction[:7]}{tzSuffix}"
        return datetime.fromisoformat(normalized)

    ns = {
        "kml": "http://www.opengis.net/kml/2.2",
        "gx": "http://www.google.com/kml/ext/2.2"
    }

    tree = ET.parse(trackFile)
    root = tree.getroot()

    # Extract ExtendedData values
    flightMeta = FlightMeta()
    extended = root.find(".//kml:ExtendedData", ns)
    if extended is not None:
        for data in extended.findall("kml:Data", ns):
            name = data.attrib.get("name")
            value = data.findtext("kml:value", default="", namespaces=ns)
            if name == "tailNumber":
                flightMeta.TailNumber = value
            elif name == "pilotName":
                flightMeta.Pilot = value
            elif name == "GPSModelName":
                flightMeta.GPSSource = value
            elif name == "source":
                flightMeta.ImportedFrom = value
            elif name == "flightTitle":
                flightTitle = value.strip()
                if ' - ' in flightTitle:
                    origin, destination = [x.strip() for x in flightTitle.split(' - ', 1)]
                    flightMeta.DerivedOrigin = origin
                    flightMeta.DerivedDestination = destination
                elif flightTitle:
                    flightMeta.DerivedOrigin = flightTitle
                    flightMeta.DerivedDestination = flightTitle

    fdrFlight = FdrFlight()
    fdrFlight.TAIL = flightMeta.TailNumber or "UNKNOWN"
    fdrFlight.timezone = config.timezoneKML if config.timezoneKML is not None else config.timezone
    fdrFlight.metaData = flightMeta

    # Find the <Placemark> with <gx:Track> and no <name>
    trackPlacemark = None
    for placemark in root.findall(".//kml:Placemark", ns):
        name = placemark.find("kml:name", ns)
        if name is None or (name.text or "").strip() == "":
            if placemark.find("gx:Track", ns) is not None:
                trackPlacemark = placemark
                break

    if trackPlacemark is None:
        raise ValueError("No valid <Placemark> with <gx:Track> found")

    track = trackPlacemark.find("gx:Track", ns)
    if track is None:
        raise ValueError("gx:Track missing inside placemark")

    times = [normalizeTimestamp(when.text or "")
             for when in track.findall("kml:when", ns)]
    coords = [list(map(float, (c.text or "").strip().split()))
              for c in track.findall("gx:coord", ns)]

    extras = {}
    for arr in (extended.findall(".//gx:SimpleArrayData", ns) if extended is not None else []):
        key = arr.attrib.get("name")
        values = [float(v.text or "0") for v in arr.findall("gx:value", ns)]
        extras[key] = values

    for i, (time, coord) in enumerate(zip(times, coords)):
        fdrFlight.trackData.append({
            'Timestamp': time.timestamp(),
            'Latitude': coord[1],
            'Longitude': coord[0],
            'Altitude': coord[2] * 3.280839895,
            'Course': extras.get("course", [0])[i],
            'Pitch': extras.get("pitch", [0])[i],
            'Bank': extras.get("bank", [0])[i],
            'Speed': extras.get("speed_kts", [0])[i],
        })

    return fdrFlight


def parseGpxFile(config:Config, trackFile:TextIO) -> FdrFlight:
    # gpx = ET.fromstringlist(trackFile.readlines())
    raise NotImplementedError


def latitudeDegreesToNm(deltaLatitudeDegrees: float) -> float:
    return deltaLatitudeDegrees * 60.0


def longitudeDegreesToNm(deltaLongitudeDegrees: float, atLatitudeDegrees: float) -> float:
    return deltaLongitudeDegrees * 60.0 * math.cos(math.radians(atLatitudeDegrees))


def longitudeDegreesForNm(distanceNm: float, atLatitudeDegrees: float) -> float:
    cosLatitude = abs(math.cos(math.radians(atLatitudeDegrees)))
    if cosLatitude < 1e-12:
        return 180.0
    return distanceNm / (60.0 * cosLatitude)


def greatCircleDistanceNm(lat1Deg: float, lon1Deg: float, lat2Deg: float, lon2Deg: float) -> float:
    lat1 = math.radians(lat1Deg)
    lon1 = math.radians(lon1Deg)
    lat2 = math.radians(lat2Deg)
    lon2 = math.radians(lon2Deg)
    deltaLat = lat2 - lat1
    deltaLong = lon2 - lon1

    a = math.sin(deltaLat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * (math.sin(deltaLong / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    earthRadiusNm = 3440.065
    return earthRadiusNm * c


def timezoneOffsetInSeconds(s: str) -> float:
    """Offset from local time to Zulu, in seconds (added to timestamps).

    Accepts decimal hours (e.g. ``3``, ``-5.5``) or ``+/-hh:mm[:ss]`` as in the README.
    """
    s = s.strip()
    if not s:
        return 0.0
    if ':' not in s:
        return float(s) * 3600
    indexAfterSign = int(s[0] in ['+', '-'])
    zone = s[indexAfterSign:].split(':')
    seconds = float(zone.pop())
    seconds += float(zone.pop()) * 60
    if len(zone):
        seconds += float(zone.pop()) * 3600
    else:
        seconds *= 60
    seconds *= -1 if s[0] == '-' else 1
    return seconds


def wrapHeading(degrees:float):
    return degrees % 360


def wrapAttitude(degrees:float):
    mod = 360 if degrees >= 0 else -360
    degrees = degrees % mod
    if degrees > 180:
        return degrees - 360
    elif degrees < -180:
        return degrees + 360
    else:
        return degrees


def toMDY(time:Union[datetime,date,int,str]):
    if isinstance(time, str):
        time = int(time)
    if isinstance(time, int):
        time = datetime.fromtimestamp(time / 1000)
    return time.strftime('%m/%d/%Y')


def toYMD(time:Union[datetime,int,str]):
    if isinstance(time, str):
        time = int(time)
    if isinstance(time, int):
        time = datetime.fromtimestamp(time / 1000)
    return time.strftime('%Y/%m/%d')


def toHM(time:Union[datetime,int,str]):
    if isinstance(time, str):
        time = int(time)
    if isinstance(time, int):
        time = datetime.fromtimestamp(time / 1000)
    return time.strftime('%H:%M')


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except ConfigError as e:
        print(f"[Error] Invalid configuration: {e}")
        sys.exit(2)
    except Exception as e:
        print(f"[Unexpected Error] {e}")
    sys.exit(1)