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


class FileType(Enum):
    UNKNOWN = 0
    CSV = 1
    KML = 2
    GPX = 3


class CardinalOffset:
    eastFt: float
    northFt: float
    upFt: float

    def __init__(self, eastFt: float, northFt: float, upFt: float):
        self.eastFt = eastFt
        self.northFt = northFt
        self.upFt = upFt


class GeodeticOffset:
    deltaLatitude: float
    deltaLongitude: float
    deltaAltitude: float

    def __init__(self, deltaLatitude: float, deltaLongitude: float, deltaAltitude: float):
        self.deltaLatitude = deltaLatitude
        self.deltaLongitude = deltaLongitude
        self.deltaAltitude = deltaAltitude


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
    offset: "CardinalOffset"
    innerRadiusNm: float
    outerRadiusNm: float
    lattitude: Optional[float]
    longitude: Optional[float]

    def __init__(
        self,
        code: str,
        offset: "CardinalOffset",
        innerRadiusNm: float,
        outerRadiusNm: float,
        lattitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ):
        self.code = code
        self.offset = offset
        self.innerRadiusNm = max(0.0, innerRadiusNm)
        self.outerRadiusNm = max(self.innerRadiusNm, outerRadiusNm)
        self.lattitude = lattitude
        self.longitude = longitude

    def hasCoordinates(self) -> bool:
        return self.lattitude is not None and self.longitude is not None


class OurAirportsRecord:
    ident: str
    gpsCode: str
    localCode: str
    iataCode: str
    name: str
    lattitude: float
    longitude: float

    def __init__(
        self,
        ident: str,
        gpsCode: str,
        localCode: str,
        iataCode: str,
        name: str,
        lattitude: float,
        longitude: float,
    ):
        self.ident = ident
        self.gpsCode = gpsCode
        self.localCode = localCode
        self.iataCode = iataCode
        self.name = name
        self.lattitude = lattitude
        self.longitude = longitude


class Config():
    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf'
    outPath:str = '.'
    timezone:float = 0
    timezoneCSV:Optional[float] = None
    timezoneKML:Optional[float] = None
    offsetOrig: Optional[CardinalOffset] = None
    offsetDest: Optional[CardinalOffset] = None

    file:Optional[configparser.RawConfigParser] = None
    configuredWaypoints: List["WaypointEntry"]
    airfieldDbPath: Optional[Path]
    airfieldDbEnabled: bool
    airfieldDbMaxAgeDays: float
    airfieldGridCellNm: float
    _airfieldRecords: Optional[List["OurAirportsRecord"]]

    OFFSET_INNER_RADIUS_NM = 2.0
    OFFSET_OUTER_RADIUS_NM = 8.0
    AIRFIELD_DB_DEFAULT_FILENAME = 'OurAirports.csv'
    AIRFIELD_DB_DEFAULT_MAX_AGE_DAYS = 90.0
    AIRFIELD_DB_URL = 'https://davidmegginson.github.io/ourairports-data/airports.csv'
    AIRFIELD_GRID_CELL_DEFAULT_NM = 120.0
    _XYZ_OFFSET_RE = re.compile(
        r'^\s*([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*$'
    )
    _AIRCRAFT_TAIL_SECTION_PREFIX = 'aircraft '
    _WAYPOINT_SECTION_PREFIX = 'waypoint '


    def __init__(self, cliArgs:argparse.Namespace):
        self.file = configparser.RawConfigParser(inline_comment_prefixes=(';'))
        configFile = self.findConfigFile(cliArgs.config)
        if configFile:
            self.file.read(configFile)

        defaults = self.file['Defaults'] if 'Defaults' in self.file else {}

        self.cliAircraft = False
        if cliArgs.aircraft:
            self.aircraft = cliArgs.aircraft.replace('\\', '/')
            self.cliAircraft = True
        elif 'aircraft' in defaults:
            self.aircraft = defaults['aircraft'].replace('\\', '/')

        if cliArgs.timezone:
            self.timezone = timezoneOffsetSeconds(cliArgs.timezone)
        else:
            if 'timezone' in defaults:
                self.timezone = timezoneOffsetSeconds(defaults['timezone'])
            if 'timezonecsv' in defaults:
                self.timezoneCSV = timezoneOffsetSeconds(defaults['timezonecsv'])
            if 'timezonekml' in defaults:
                self.timezoneKML = timezoneOffsetSeconds(defaults['timezonekml'])

        if cliArgs.outputFolder:
            self.outPath = os.path.expanduser(cliArgs.outputFolder)
        elif 'outpath' in defaults:
            self.outPath = os.path.expanduser(defaults['outpath'])

        self.airfieldDbEnabled = cliArgs.airfieldDB is not None
        self.airfieldDbPath = self._resolveAirfieldDbPath(cliArgs.airfieldDB) if self.airfieldDbEnabled else None
        self.airfieldDbMaxAgeDays = self._getFloatDefault(
            defaults=defaults,
            key='airfielddbmaxagedays',
            fallback=self.AIRFIELD_DB_DEFAULT_MAX_AGE_DAYS,
            minimum=0.0,
            warningPrefix='airfielddbmaxagedays',
            legacyKey='airportdbmaxagedays',
        )
        self.airfieldGridCellNm = self._getFloatDefault(
            defaults=defaults,
            key='airfieldgridcellnm',
            fallback=self.AIRFIELD_GRID_CELL_DEFAULT_NM,
            minimum=1.0,
            warningPrefix='airfieldgridcellnm',
        )
        self._airfieldRecords = None

        self.configuredWaypoints = self._loadWaypoints()
        if cliArgs.offsetOrig:
            self.offsetOrig = self.parseOffset(cliArgs.offsetOrig)
        if cliArgs.offsetDest:
            self.offsetDest = self.parseOffset(cliArgs.offsetDest)


    def offsetHelperForFlight(
        self,
        flight: "FdrFlight",
        boundingBoxes: List["BoundingBox"],
    ) -> "AirportOffsetHelper":
        helper = AirportOffsetHelper()
        candidateAirfields = self._prefilterAirfieldsForFlight(boundingBoxes)

        for entry in self._resolvedWaypointsForFlight(boundingBoxes, candidateAirfields):
            if entry.lattitude is None or entry.longitude is None:
                continue
            helper.addAirport(
                code=entry.code,
                lattitude=entry.lattitude,
                longitude=entry.longitude,
                offset=entry.offset,
                innerRadiusNm=entry.innerRadiusNm,
                outerRadiusNm=entry.outerRadiusNm,
            )

        self._addCliAirportOffsets(helper, flight)
        return helper


    def _addCliAirportOffsets(self, helper: "AirportOffsetHelper", flight: "FdrFlight") -> None:
        flightMeta = flight.metaData
        firstLat, firstLon, lastLat, lastLon = firstLastTrackPosition(flight.trackData)

        if self.offsetOrig is not None and firstLat is not None and firstLon is not None:
            offset = self.offsetOrig
            code = (flightMeta.DerivedOrigin if flightMeta and flightMeta.DerivedOrigin else "ORIG").strip() or "ORIG"
            helper.addAirport(
                code=code,
                lattitude=firstLat,
                longitude=firstLon,
                offset=offset,
                innerRadiusNm=self.OFFSET_INNER_RADIUS_NM,
                outerRadiusNm=self.OFFSET_OUTER_RADIUS_NM,
            )
        if self.offsetDest is not None and lastLat is not None and lastLon is not None:
            offset = self.offsetDest
            code = (flightMeta.DerivedDestination if flightMeta and flightMeta.DerivedDestination else "DEST").strip() or "DEST"
            helper.addAirport(
                code=code,
                lattitude=lastLat,
                longitude=lastLon,
                offset=offset,
                innerRadiusNm=self.OFFSET_INNER_RADIUS_NM,
                outerRadiusNm=self.OFFSET_OUTER_RADIUS_NM,
            )


    def _resolvedWaypointsForFlight(
        self,
        boundingBoxes: List["BoundingBox"],
        candidateAirfields: List["OurAirportsRecord"],
    ) -> List["WaypointEntry"]:
        entries: List[WaypointEntry] = []
        lookupMap = self._airfieldLookupMap(candidateAirfields)

        for waypoint in self.configuredWaypoints:
            resolved = self._resolveWaypoint(waypoint, lookupMap)
            if resolved is None or resolved.lattitude is None or resolved.longitude is None:
                continue
            if not self._isInFlightBounds(resolved.lattitude, resolved.longitude, boundingBoxes, resolved.outerRadiusNm):
                continue
            entries.append(resolved)
        return entries


    def _resolveWaypoint(
        self,
        waypoint: "WaypointEntry",
        lookupMap: Dict[str, "OurAirportsRecord"],
    ) -> Optional["WaypointEntry"]:
        if waypoint.hasCoordinates():
            return waypoint

        if not self.airfieldDbEnabled:
            self._warnConfig(f"Skipping [Waypoint {waypoint.code}] because lat/lon are required unless --airfieldDB is enabled.")
            return None

        record = lookupMap.get(waypoint.code.upper())
        if record is None:
            self._warnConfig(
                f"Skipping [Waypoint {waypoint.code}] because no matching airfield was found in {self.airfieldDbPath}."
            )
            return None
        return WaypointEntry(
            code=waypoint.code,
            offset=waypoint.offset,
            innerRadiusNm=waypoint.innerRadiusNm,
            outerRadiusNm=waypoint.outerRadiusNm,
            lattitude=record.lattitude,
            longitude=record.longitude,
        )


    @staticmethod
    def _isInFlightBounds(latitude: float, longitude: float, boundingBoxes: List["BoundingBox"], radiusNm: float) -> bool:
        if not boundingBoxes:
            return True
        for box in boundingBoxes:
            if box.contains(latitude, longitude, marginNm=radiusNm):
                return True
        return False


    def _airfieldLookupMap(self, records: List["OurAirportsRecord"]) -> Dict[str, "OurAirportsRecord"]:
        lookup: Dict[str, OurAirportsRecord] = {}
        for record in records:
            for key in [record.ident, record.gpsCode, record.localCode, record.iataCode]:
                normalized = key.strip().upper()
                if normalized and normalized not in lookup:
                    lookup[normalized] = record
        return lookup


    def _prefilterAirfieldsForFlight(self, boundingBoxes: List["BoundingBox"]) -> List["OurAirportsRecord"]:
        records = self._loadAirfieldRecords()
        if not records:
            return []
        if not boundingBoxes:
            return records

        maxOuterRadius = max(
            [self.OFFSET_OUTER_RADIUS_NM]
            + [waypoint.outerRadiusNm for waypoint in self.configuredWaypoints]
        )
        return [
            record for record in records
            if self._isInFlightBounds(record.lattitude, record.longitude, boundingBoxes, maxOuterRadius)
        ]


    def _loadAirfieldRecords(self) -> List["OurAirportsRecord"]:
        if not self.airfieldDbEnabled or self.airfieldDbPath is None:
            return []
        if self._airfieldRecords is not None:
            return self._airfieldRecords

        dbPath = self.airfieldDbPath
        dbExists = dbPath.is_file()
        if not dbExists:
            self._warnConfig(f"Airfield DB not found at {dbPath}; downloading OurAirports data.")
            self._downloadAirfieldDb(dbPath)
            dbExists = dbPath.is_file()

        if dbExists and self._isAirfieldDbStale(dbPath):
            self._warnConfig(
                f"Airfield DB is older than {self.airfieldDbMaxAgeDays:g} days: {dbPath}. Attempting refresh."
            )
            try:
                self._downloadAirfieldDb(dbPath)
            except Exception as err:
                self._warnConfig(f"Failed to refresh airfield DB ({err}); continuing with stale data.")
        elif not dbExists:
            self._warnConfig(f"Airfield DB download failed and lookup will be skipped: {dbPath}")
            self._airfieldRecords = []
            return self._airfieldRecords

        self._airfieldRecords = self._readOurAirportsCsv(dbPath)
        return self._airfieldRecords


    def _downloadAirfieldDb(self, dbPath: Path) -> None:
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


    def _resolveAirfieldDbPath(self, dbValue: Optional[str]) -> Optional[Path]:
        if dbValue is None:
            return None
        if dbValue == '':
            return Path(os.path.dirname(os.path.abspath(__file__))) / self.AIRFIELD_DB_DEFAULT_FILENAME

        pathString = dbValue.strip()
        path = Path(pathString).expanduser()
        if pathString.endswith(('/', '\\')) or (path.exists() and path.is_dir()) or path.suffix == '':
            path = path / self.AIRFIELD_DB_DEFAULT_FILENAME
        return path


    def _getFloatDefault(
        self,
        defaults: Any,
        key: str,
        fallback: float,
        minimum: Optional[float],
        warningPrefix: str,
        legacyKey: Optional[str] = None,
    ) -> float:
        rawValue = None
        usedKey = key
        if key in defaults:
            rawValue = defaults[key]
        elif legacyKey and legacyKey in defaults:
            rawValue = defaults[legacyKey]
            usedKey = legacyKey
        if rawValue is None:
            return fallback

        try:
            value = float(rawValue)
        except ValueError:
            self._warnConfig(f"Ignoring invalid {usedKey} value {rawValue!r}; using default {fallback}.")
            return fallback
        if minimum is not None and value < minimum:
            self._warnConfig(f"Ignoring invalid {usedKey} value {rawValue!r}; minimum is {minimum}.")
            return fallback
        return value


    def acftByTail(self, tailNumber:str):
        if not self.cliAircraft and self.file:
            for section in self.file.sections():
                if section.lower().replace('\\', '/').startswith('aircraft/'):
                    aircraft = self.file[section]
                    if tailNumber in [tail.strip() for tail in aircraft['Tails'].split(',')]:
                        return section

        # If no aircraft is provided via CLI or config, or if no matching aircraft section is found, return the default aircraft
        return self.aircraft


    def aircraftPathForTail(self, tailNumber: str) -> str:
        section = self.acftByTail(tailNumber)
        return section.replace('\\', '/') if section else self.aircraft


    def drefsByTail(self, tailNumber: str) -> Tuple[Dict[str, str], List[str]]:
        sources: Dict[str, str] = {}
        defines: List[str] = []

        def add(instrument: str, value: str, scale: str = '1.0', name: Optional[str] = None):
            name = name or instrument.rpartition('/')[2][:FdrColumnWidth]
            sources[name] = value
            defines.append(f'{instrument}\t{scale}\t\t// source: {value}')

        def fromSection(sectionName: str):
            if self.file and sectionName in self.file:
                for key, val in self.file[sectionName].items():
                    if key.lower().startswith('dref '):
                        instrument, expr, scale, name = parseDrefConfig(key, val)
                        add(instrument, expr, scale, name)

        def parseDrefConfig(key: str, val: str) -> Tuple[str, str, str, Optional[str]]:
            if not key.lower().startswith('dref '):
                raise ValueError(f"Invalid DREF key (must start with 'DREF '): {key}")

            instrument = key[5:].strip().replace('\\', '/')

            exprEnd = None
            depth = 0
            for i, char in enumerate(val):
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                    if depth < 0:
                        raise ValueError(f"Unmatched closing parenthesis in DREF expression: {val}")
                elif char == ',' and depth == 0:
                    exprEnd = i
                    break

            if depth != 0:
                raise ValueError(f"Unmatched parenthesis in DREF expression: {val}")

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

        tailSection = self._aircraftTailSectionForTail(tailNumber)
        fromSection('Defaults')
        fromSection(self.acftByTail(tailNumber))
        fromSection(tailNumber)
        if tailSection is not None:
            fromSection(tailSection)

        return sources, defines


    _TAIL_TRIM_KEYS = frozenset[str]({'headingtrim', 'pitchtrim', 'rolltrim'})
    def tail(self, tailNumber:str):
        tailConfig = {}
        aircraftTailSectionName = self._aircraftTailSectionForTail(tailNumber)
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

            if aircraftTailSectionName is not None:
                section = self.file[aircraftTailSectionName]
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


    def _aircraftTailSectionForTail(self, tailNumber: str) -> Optional[str]:
        if not self.file:
            return None

        match = f'{self._AIRCRAFT_TAIL_SECTION_PREFIX}{tailNumber}'.lower()
        for section in self.file.sections():
            if section.lower() == match:
                return section
        return None


    def _loadWaypoints(self) -> List["WaypointEntry"]:
        entries: List["WaypointEntry"] = []
        if not self.file:
            return entries

        for section in self.file.sections():
            if not section.lower().startswith(self._WAYPOINT_SECTION_PREFIX):
                continue

            waypointName = section[len(self._WAYPOINT_SECTION_PREFIX):].strip()
            if not waypointName:
                self._warnConfig(f"Ignoring [{section}] because it does not include a waypoint name.")
                continue

            sectionData = self.file[section]
            latRaw = sectionData.get('lat')
            lonRaw = sectionData.get('lon')
            offsetRaw = sectionData.get('offset')

            if offsetRaw is None:
                self._warnConfig(f"Skipping [{section}] because offset is required.")
                continue

            lat = None
            lon = None
            if latRaw is None or lonRaw is None:
                if not self.airfieldDbEnabled:
                    self._warnConfig(f"Skipping [{section}] because both lat and lon are required in phase 1.")
                    continue
            else:
                try:
                    lat = float(latRaw)
                    lon = float(lonRaw)
                except ValueError:
                    self._warnConfig(f"Skipping [{section}] because lat/lon must be numeric.")
                    continue

            try:
                offset = self.parseOffset(offsetRaw)
            except ValueError as err:
                self._warnConfig(f"Skipping [{section}] because offset is invalid: {err}")
                continue

            innerRadiusNm = self._parseWaypointRadius(
                sectionName=section,
                key='innerradiusnm',
                defaultValue=self.OFFSET_INNER_RADIUS_NM,
            )
            outerRadiusNm = self._parseWaypointRadius(
                sectionName=section,
                key='outerradiusnm',
                defaultValue=self.OFFSET_OUTER_RADIUS_NM,
            )

            entries.append(
                WaypointEntry(
                    code=waypointName,
                    offset=offset,
                    innerRadiusNm=innerRadiusNm,
                    outerRadiusNm=outerRadiusNm,
                    lattitude=lat,
                    longitude=lon,
                )
            )

        return entries


    def _parseWaypointRadius(self, sectionName: str, key: str, defaultValue: float) -> float:
        if not self.file or sectionName not in self.file:
            return defaultValue

        valueRaw = self.file[sectionName].get(key)
        if valueRaw is None:
            return defaultValue

        try:
            return float(valueRaw)
        except ValueError:
            self._warnConfig(
                f"Ignoring invalid {key} value in [{sectionName}]: {valueRaw!r}. Using default {defaultValue}."
            )
            return defaultValue


    @staticmethod
    def _warnConfig(message: str) -> None:
        print(f"Config warning: {message}", file=sys.stderr)


    def findConfigFile(self, cliPath:str):
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


    @classmethod
    def parseOffset(cls, s: str) -> CardinalOffset:
        """Parse ``east, north, up`` with optional sign on each (all feet)."""
        m = cls._XYZ_OFFSET_RE.match(s.strip())
        if not m:
            raise ValueError(
                f"Invalid offset {s!r}; expected east,north,up in feet (three comma-separated numbers, optional +/- per value)"
            )
        return CardinalOffset(float(m.group(1)), float(m.group(2)), float(m.group(3)))


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


class AirportOffsetHelper:
    _entries: List[WaypointEntry]


    def __init__(self):
        self._entries = []


    def addAirport(
        self,
        code: str,
        lattitude: float,
        longitude: float,
        offset: CardinalOffset,
        innerRadiusNm: float,
        outerRadiusNm: float,
    ) -> None:
        self._entries.append(
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

        for entry in self._entries:
            if entry.lattitude is None or entry.longitude is None:
                continue
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
                ringWidthNm = entry.outerRadiusNm - entry.innerRadiusNm
                if ringWidthNm <= 0:
                    continue
                localWeight = (entry.outerRadiusNm - distanceNm) / ringWidthNm
                localWeight = max(0.0, min(1.0, localWeight))
                if localWeight <= 0:
                    continue
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
        if not distances:
            return []
        epsilon = 1e-12
        zeroDistanceIndexes = [i for i, distance in enumerate(distances) if distance <= epsilon]
        if zeroDistanceIndexes:
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


class FdrTrackPoint():
    TIME:datetime
    LONG:float
    LAT:float
    ALTMSL:float
    HEADING:float
    PITCH:float
    ROLL:float
    offset: Optional[GeodeticOffset]

    drefs: Dict[str, float]


    def __init__(self, time:datetime, longitude:float, latitude:float, altitude:float, heading:float, pitch:float, roll:float):
        self.TIME = time
        self.LONG = longitude
        self.LAT = latitude
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
            return (self.LONG, self.LAT, self.ALTMSL)
        return (
            self.LONG + self.offset.deltaLongitude,
            self.LAT + self.offset.deltaLatitude,
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


    def buildBoundingBoxes(self, cellSizeNm: float) -> List[BoundingBox]:
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
        tailConfig = config.tail(self.TAIL)
        drefSources, _ = config.drefsByTail(self.TAIL)
        boundingBoxes = self.buildBoundingBoxes(config.airfieldGridCellNm)
        offsetHelper = config.offsetHelperForFlight(self, boundingBoxes)

        for trackData in self.trackData:
            point = FdrTrackPoint(
                time      = datetime.fromtimestamp(float(trackData['Timestamp']) + self.timezone),
                longitude = float(trackData['Longitude']),
                latitude  = float(trackData['Latitude']),
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
        waypoints    = flightMeta.RouteWaypoints or "N/A"

        startTime = flightMeta.StartTime
        endTime   = flightMeta.EndTime
        ymd       = toYMD(startTime) if startTime is not None else "N/A"
        startHM   = toHM(startTime) if startTime is not None else "--:--"
        endHM     = toHM(endTime) if endTime is not None else "--:--"
        startLat  = str(self.roundLatLong(float(flightMeta.StartLatitude))) if flightMeta.StartLatitude is not None else "N/A"
        startLong = str(self.roundLatLong(float(flightMeta.StartLongitude))) if flightMeta.StartLongitude is not None else "N/A"
        endLat    = str(self.roundLatLong(float(flightMeta.EndLatitude))) if flightMeta.EndLatitude is not None else "N/A"
        endLong   = str(self.roundLatLong(float(flightMeta.EndLongitude))) if flightMeta.EndLongitude is not None else "N/A"

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
 Planned: {waypoints}
GPS/AHRS: {flightMeta.GPSSource}''' + clientLine + importedLine


    @staticmethod
    def roundLatLong(value: float) -> float:
        return round(value, 9)

    @staticmethod
    def roundAltitude(value: float) -> float:
        return round(value, 4)

    @staticmethod
    def roundAttitude(value: float) -> float:
        return round(value, 3)

    @staticmethod
    def roundHeading(value: float) -> float:
        return round(value, 3)



def main(argv:List[str]):
    parser = argparse.ArgumentParser(
        description='Convert ForeFlight compatible track files into X-Plane compatible FDR files',
        epilog='Example: python 42fdr.py tracklog-1.csv tracklog-2.kml'
    )

    parser.add_argument('-a', '--aircraft', default=None, help='Path to default X-Plane aircraft')
    parser.add_argument('-c', '--config', default=None, help='Path to 42fdr config file')
    parser.add_argument('-t', '--timezone', default=None, help='An offset to add to all times processed.  +/-hh:mm[:ss] or +/-<decimal hours>')
    parser.add_argument('-o', '--outputFolder', default=None, dest='outputFolder', help='Path to write X-Plane compatible FDR v4 output file')
    parser.add_argument('--airfieldDB', nargs='?', default=None, const='', metavar='PATH', help='Enable local airfield lookup using OurAirports data. Optional path may be a CSV file or directory.')
    parser.add_argument('--oo', default=None, dest='offsetOrig', metavar='EAST,NORTH,UP', help='Position offset at origin airport in feet: east, north, up. Use with --od for airport-aware blending.')
    parser.add_argument('--od', default=None, dest='offsetDest', metavar='EAST,NORTH,UP', help='Position offset at destination airport in feet; same format as --oo')
    parser.add_argument('trackfile', default=None, nargs='+', help='Path to one or more ForeFlight compatible track files (CSV, KML)')
    args = parser.parse_args()
    
    config = Config(args)
    for inPath in args.trackfile:
        inPath = os.path.expanduser(inPath)
        trackFile = open(inPath, 'r')
        fdrFlight = parseInputFile(config, trackFile)

        if fdrFlight is not None:
            fdrFlight.buildTrackPoints(config)
            fdrFlight.deriveMissingMetaData()
            outPath = getOutpath(config, inPath, fdrFlight)
            with open(outPath, 'w') as fdrFile:
                writeOutputFile(config, fdrFile, fdrFlight)
        else:
            print(f"No flight data found in {inPath}")
    return 0


def getOutpath(config:Config, inPath:str, fdrFlight:FdrFlight):
    filename = os.path.basename(inPath)
    outPath = config.outPath or '.'
    return Path(os.path.join(outPath, filename)).with_suffix('.fdr')


def parseInputFile(config:Config, trackFile:TextIO) -> Optional[FdrFlight]:
    try:
        filetype = getFiletype(trackFile)

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


def getFiletype(file:TextIO) -> FileType:
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
    metaCols.remove('Battery State') # Bug in ForeFlight

    # Read the metadata values row
    metaVals = readCsvRow(csvReader)
    if metaVals is None:
        raise ValueError('CSV file is missing the metadata values row')

    # Populate flight metadata
    metaData = dict(zip(metaCols, metaVals))
    for colName in metaData:
        colValue = metaData[colName]
        if colName == 'Tail Number':
            flightMeta.TailNumber = colValue;
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

    times = [datetime.fromisoformat((when.text or "").replace("Z", "+00:00"))
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


def writeOutputFile(config:Config, fdrFile:TextIO, fdrFlight:FdrFlight):
    timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M:%SZ')
    drefSources, drefDefines = config.drefsByTail(fdrFlight.TAIL)

    tzOffset = fdrFlight.timezone
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
        fdrComment(f'Generated on [{timestamp}]'),
        fdrComment(f'This X-Plane compatible FDR file was converted from a ForeFlight track file using 42fdr.py'),
        fdrComment('https://github.com/MadReasonable/42fdr'),
        '\n',
        fdrComment(tzOffsetExplanation),
        '\n',
        fdrComment(fdrFlight.summary()),
        '\n\n',
        fdrComment("Fields below define general data for this flight."),
        fdrComment("ForeFlight only provides a few of the data points that X-Plane can accept.") ,
        '\n',
        f'ACFT, {config.aircraftPathForTail(fdrFlight.TAIL)}\n',
        f'TAIL, {fdrFlight.TAIL}\n',
        f'DATE, {toMDY(fdrFlight.DATE)}\n',
        '\n\n',
        fdrComment('DREFs below (if any) define additional columns beyond the 7th (Roll)'),
        fdrComment('in the flight track data that follows.'),
        '\n',
        fdrDrefs(drefDefines),
        '\n\n',
        fdrComment('The remainder of this file consists of GPS/AHRS track points.'),
        '\n',
        fdrColNames(drefSources.keys()),
    ])

    for point in fdrFlight.track:
        outLong, outLat, outAltMSL = point.renderPosition()
        time    = point.TIME.strftime('%H:%M:%S.%f')
        long    = str.rjust(str(fdrFlight.roundLatLong(outLong)), FdrColumnWidth)
        lat     = str.rjust(str(fdrFlight.roundLatLong(outLat)), FdrColumnWidth)
        altMSL  = str.rjust(str(fdrFlight.roundAltitude(outAltMSL)), FdrColumnWidth)
        heading = str.rjust(str(fdrFlight.roundHeading(point.HEADING)), FdrColumnWidth)
        pitch   = str.rjust(str(fdrFlight.roundAttitude(point.PITCH)), FdrColumnWidth)
        roll    = str.rjust(str(fdrFlight.roundAttitude(point.ROLL)), FdrColumnWidth)
        fdrFile.write(f'{time}, {long}, {lat}, {altMSL}, {heading}, {pitch}, {roll}')

        drefValues = []
        for dref in drefSources:
            drefValues.append(str.rjust(str(point.drefs[dref]), FdrColumnWidth))
        fdrFile.write(', '+ ', '.join(drefValues) +'\n')


def fdrComment(comment:str):
    return 'COMM, '+ '\nCOMM, '.join(comment.splitlines()) +'\n'


def fdrDrefs(drefDefines:List[str]):
    return 'DREF, ' + '\nDREF, '.join(drefDefines) +'\n'


def fdrColNames(drefNames:Iterable[str]):
    names = '''COMM,                        degrees,             degrees,              ft msl,                 deg,                 deg,                 deg
COMM,                      Longitude,            Latitude,              AltMSL,             Heading,               Pitch,                Roll'''

    for drefName in drefNames:
        names += ', '+ str.rjust(drefName, FdrColumnWidth)

    return names +'\n'


def firstLastTrackPosition(trackData: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if not trackData:
        return (None, None, None, None)
    first = trackData[0]
    last = trackData[-1]
    return (
        float(first['Latitude']),
        float(first['Longitude']),
        float(last['Latitude']),
        float(last['Longitude']),
    )


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


def timezoneOffsetSeconds(s: str) -> float:
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
    except FileNotFoundError as e:
        print(f"[Error] File not found: {e.filename}")
        sys.exit(3)
    except ValueError as e:
        print(f"[Error] Invalid input: {e}")
        sys.exit(2)
    except Exception as e:
        print(f"[Unexpected Error] {e}")
    sys.exit(1)