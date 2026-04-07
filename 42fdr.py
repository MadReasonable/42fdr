#!/usr/bin/env python3
import argparse, configparser, csv, os, re, sys, xml.etree.ElementTree as ET
import math  # Used when evaluating user DREF value expressions
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, Tuple, Union


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


class Config():
    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf'
    outPath:str = '.'
    timezone:float = 0
    timezoneCSV:Optional[float] = None
    timezoneKML:Optional[float] = None
    offsetOrig: Optional[CardinalOffset] = None
    offsetDest: Optional[CardinalOffset] = None

    file:Optional[configparser.RawConfigParser] = None

    OFFSET_INNER_RADIUS_NM = 2.0
    OFFSET_OUTER_RADIUS_NM = 8.0
    _XYZ_OFFSET_RE = re.compile(
        r'^\s*([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*,\s*'
        r'([+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?)\s*$'
    )


    def __init__(self, cliArgs:argparse.Namespace):
        self.file = configparser.RawConfigParser()
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
            self.outPath = cliArgs.outputFolder
        elif 'outpath' in defaults:
            self.outPath = defaults['outpath']

        if cliArgs.offset_orig:
            self.offsetOrig = self.parseOffset(cliArgs.offset_orig)
        if cliArgs.offset_dest:
            self.offsetDest = self.parseOffset(cliArgs.offset_dest)


    def airportOffsetsForFlight(
        self,
        flightMeta: Optional["FlightMeta"],
        trackData: List[Dict[str, Any]],
    ) -> "AirportOffsetHelper":
        helper = AirportOffsetHelper()
        first_lat, first_lon, last_lat, last_lon = firstLastTrackPosition(trackData)

        if first_lat is None and flightMeta and flightMeta.StartLatitude is not None and flightMeta.StartLongitude is not None:
            first_lat = float(flightMeta.StartLatitude)
            first_lon = float(flightMeta.StartLongitude)
        if last_lat is None and flightMeta and flightMeta.EndLatitude is not None and flightMeta.EndLongitude is not None:
            last_lat = float(flightMeta.EndLatitude)
            last_lon = float(flightMeta.EndLongitude)

        if self.offsetOrig is not None and first_lat is not None and first_lon is not None:
            offset = self.offsetOrig
            code = (flightMeta.DerivedOrigin if flightMeta and flightMeta.DerivedOrigin else "ORIG").strip() or "ORIG"
            helper.add_airport(
                code=code,
                lat_deg=first_lat,
                lon_deg=first_lon,
                offset=offset,
                inner_radius_nm=self.OFFSET_INNER_RADIUS_NM,
                outer_radius_nm=self.OFFSET_OUTER_RADIUS_NM,
            )

        if self.offsetDest is not None and last_lat is not None and last_lon is not None:
            offset = self.offsetDest
            code = (flightMeta.DerivedDestination if flightMeta and flightMeta.DerivedDestination else "DEST").strip() or "DEST"
            helper.add_airport(
                code=code,
                lat_deg=last_lat,
                lon_deg=last_lon,
                offset=offset,
                inner_radius_nm=self.OFFSET_INNER_RADIUS_NM,
                outer_radius_nm=self.OFFSET_OUTER_RADIUS_NM,
            )

        return helper


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

        fromSection('Defaults')
        fromSection(self.acftByTail(tailNumber))
        fromSection(tailNumber)

        return sources, defines


    _TAIL_TRIM_KEYS = frozenset[str]({'headingtrim', 'pitchtrim', 'rolltrim'})
    def tail(self, tailNumber:str):
        tailConfig = {}
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

        if 'headingtrim' not in tailConfig:
            tailConfig['headingtrim'] = 0
        if 'pitchtrim' not in tailConfig:
            tailConfig['pitchtrim'] = 0
        if 'rolltrim' not in tailConfig:
            tailConfig['rolltrim'] = 0

        return tailConfig


    def findConfigFile(self, cliPath:str):
        if cliPath:
            return cliPath
        
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


class AirportOffsetEntry:
    code: str
    lat_deg: float
    lon_deg: float
    offset: CardinalOffset
    inner_radius_nm: float
    outer_radius_nm: float

    def __init__(
        self,
        code: str,
        lat_deg: float,
        lon_deg: float,
        offset: CardinalOffset,
        inner_radius_nm: float,
        outer_radius_nm: float,
    ):
        self.code = code
        self.lat_deg = lat_deg
        self.lon_deg = lon_deg
        self.offset = offset
        self.inner_radius_nm = max(0.0, inner_radius_nm)
        self.outer_radius_nm = max(self.inner_radius_nm, outer_radius_nm)


class AirportOffsetHelper:
    _entries: List[AirportOffsetEntry]


    def __init__(self):
        self._entries = []


    def add_airport(
        self,
        code: str,
        lat_deg: float,
        lon_deg: float,
        offset: CardinalOffset,
        inner_radius_nm: float,
        outer_radius_nm: float,
    ) -> None:
        self._entries.append(
            AirportOffsetEntry(
                code=code,
                lat_deg=lat_deg,
                lon_deg=lon_deg,
                offset=offset,
                inner_radius_nm=inner_radius_nm,
                outer_radius_nm=outer_radius_nm,
            )
        )


    def offsetForPosition(self, lattitude: float, longitude: float) -> Optional[GeodeticOffset]:
        cardinalOffset = self._offsetFeetForPosition(lattitude, longitude)
        if cardinalOffset is None:
            return None
        return self._cardinalToGeodeticOffset(cardinalOffset, lattitude)


    def _offsetFeetForPosition(self, lattitude: float, longitude: float) -> Optional[CardinalOffset]:
        inner_matches: List[Tuple[float, AirportOffsetEntry]] = []
        outer_matches: List[Tuple[float, AirportOffsetEntry]] = []

        for entry in self._entries:
            distance_nm = greatCircleDistanceNm(lattitude, longitude, entry.lat_deg, entry.lon_deg)
            if distance_nm <= entry.inner_radius_nm:
                inner_matches.append((distance_nm, entry))
            elif distance_nm <= entry.outer_radius_nm:
                outer_matches.append((distance_nm, entry))

        if inner_matches:
            center_distances = [distance_nm for distance_nm, _ in inner_matches]
            blend_weights = self._inverseRatioWeights(center_distances)
            total_weight = 0.0
            east_sum = 0.0
            north_sum = 0.0
            up_sum = 0.0
            for weight, (_, entry) in zip(blend_weights, inner_matches):
                total_weight += weight
                east_sum += entry.offset.eastFt * weight
                north_sum += entry.offset.northFt * weight
                up_sum += entry.offset.upFt * weight
            if total_weight > 0:
                return CardinalOffset(
                    eastFt=east_sum / total_weight,
                    northFt=north_sum / total_weight,
                    upFt=up_sum / total_weight,
                )
            return None

        if outer_matches:
            inner_edge_distances: List[float] = []
            local_offsets: List[CardinalOffset] = []
            for distance_nm, entry in outer_matches:
                ring_width_nm = entry.outer_radius_nm - entry.inner_radius_nm
                if ring_width_nm <= 0:
                    continue
                local_weight = (entry.outer_radius_nm - distance_nm) / ring_width_nm
                local_weight = max(0.0, min(1.0, local_weight))
                if local_weight <= 0:
                    continue
                inner_edge_distances.append(max(0.0, distance_nm - entry.inner_radius_nm))
                local_offsets.append(
                    CardinalOffset(
                        eastFt=entry.offset.eastFt * local_weight,
                        northFt=entry.offset.northFt * local_weight,
                        upFt=entry.offset.upFt * local_weight,
                    )
                )

            if not local_offsets:
                return None

            blend_weights = self._inverseRatioWeights(inner_edge_distances)
            total_weight = 0.0
            east_sum = 0.0
            north_sum = 0.0
            up_sum = 0.0
            for weight, local_offset in zip(blend_weights, local_offsets):
                total_weight += weight
                east_sum += local_offset.eastFt * weight
                north_sum += local_offset.northFt * weight
                up_sum += local_offset.upFt * weight
            if total_weight > 0:
                return CardinalOffset(
                    eastFt=east_sum / total_weight,
                    northFt=north_sum / total_weight,
                    upFt=up_sum / total_weight,
                )

        return None


    @staticmethod
    def _inverseRatioWeights(distances: List[float]) -> List[float]:
        if not distances:
            return []
        epsilon = 1e-12
        zero_distance_indexes = [i for i, distance in enumerate(distances) if distance <= epsilon]
        if zero_distance_indexes:
            dominant_weight = 1.0 / len(zero_distance_indexes)
            return [dominant_weight if i in zero_distance_indexes else 0.0 for i in range(len(distances))]

        longest_distance = max(distances)
        if longest_distance <= epsilon:
            uniform_weight = 1.0 / len(distances)
            return [uniform_weight for _ in distances]

        raw_weights = [longest_distance / distance for distance in distances]
        total_raw_weight = sum(raw_weights)
        if total_raw_weight <= epsilon:
            uniform_weight = 1.0 / len(distances)
            return [uniform_weight for _ in distances]
        return [weight / total_raw_weight for weight in raw_weights]


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


    def outputPosition(self) -> Tuple[float, float, float]:
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


    def buildTrackPoints(self, config: Config) -> None:
        meta = self.metaData or FlightMeta()
        tailConfig = config.tail(self.TAIL)
        drefSources, _ = config.drefsByTail(self.TAIL)
        airportOffsets = config.airportOffsetsForFlight(self.metaData, self.trackData)

        for trackData in self.trackData:
            baseLong = float(trackData['Longitude'])
            baseLat = float(trackData['Latitude'])
            baseAlt = float(trackData['Altitude'])


            point = FdrTrackPoint(
                time      = datetime.fromtimestamp(float(trackData['Timestamp']) + self.timezone),
                longitude = baseLong,
                latitude  = baseLat,
                altitude  = baseAlt,
                heading   = wrapHeading(float(trackData['Course']) + tailConfig['headingtrim']),
                pitch     = wrapAttitude(float(trackData['Pitch']) + tailConfig['pitchtrim']),
                roll      = wrapAttitude(float(trackData['Bank']) + tailConfig['rolltrim'])
            )
            point.addDrefs(drefSources, meta, trackData)

            offset = airportOffsets.offsetForPosition(baseLat, baseLong)
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
    parser.add_argument('--oo', default=None, dest='offset_orig', metavar='EAST,NORTH,UP', help='Position offset at origin airport in feet: east, north, up. Use with --od for airport-aware blending.')
    parser.add_argument('--od', default=None, dest='offset_dest', metavar='EAST,NORTH,UP', help='Position offset at destination airport in feet; same format as --oo')
    parser.add_argument('trackfile', default=None, nargs='+', help='Path to one or more ForeFlight compatible track files (CSV, KML)')
    args = parser.parse_args()
    
    config = Config(args)
    for inPath in args.trackfile:
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
        outLong, outLat, outAltMSL = point.outputPosition()
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