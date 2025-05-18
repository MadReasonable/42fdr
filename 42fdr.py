#!/usr/bin/env python3
import argparse, configparser, csv, os, re, sys, xml.etree.ElementTree as ET
import math  # Used when evaluating user DREF value expressions
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, MutableMapping, TextIO, Tuple, Union


FdrColumnWidth = 19

class FileType(Enum):
    UNKNOWN = 0
    CSV = 1
    KML = 2
    GPX = 3


class Config():
    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf'
    outPath:str = '.'
    timezone:int = 0
    timezoneCSV:int = None
    timezoneKML:int = None

    file:MutableMapping = None

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
            self.timezone = secondsFromString(cliArgs.timezone)
        else:
            if 'timezone' in defaults:
                self.timezone = secondsFromString(defaults['timezone'])
            if 'timezonecsv' in defaults:
                self.timezoneCSV = secondsFromString(defaults['timezonecsv'])
            if 'timezonekml' in defaults:
                self.timezoneKML = secondsFromString(defaults['timezonekml'])

        if cliArgs.outputFolder:
            self.outPath = cliArgs.outputFolder
        elif 'outpath' in defaults:
            self.outPath = defaults['outpath']

    def acftByTail(self, tailNumber:str):
        if self.cliAircraft:
            return None  # Aircraft passed on the command-line has priority
        for section in self.file.sections():
            if section.lower().replace('\\', '/').startswith('aircraft/'):
                aircraft = self.file[section]
                if tailNumber in [tail.strip() for tail in aircraft['Tails'].split(',')]:
                    return section
        return self.aircraft

    def aircraftPathForTail(self, tailNumber: str) -> str:
        section = self.acftByTail(tailNumber)
        return section.replace('\\', '/') if section else self.aircraft

    def drefsByTail(self, tailNumber: str) -> Tuple[Dict[str, str], List[str]]:
        sources: Dict[str, str] = {}
        defines: List[str] = []

        def add(instrument: str, value: str, scale: str = '1.0', name: str = None):
            name = name or instrument.rpartition('/')[2][:FdrColumnWidth]
            sources[name] = value
            defines.append(f'{instrument}\t{scale}\t\t// source: {value}')

        def fromSection(sectionName: str):
            if sectionName and sectionName in self.file:
                for key, val in self.file[sectionName].items():
                    if key.lower().startswith('dref '):
                        instrument, expr, scale, name = parseDrefConfig(key, val)
                        add(instrument, expr, scale, name)

        def parseDrefConfig(key: str, val: str) -> Tuple[str, str, str, Union[str, None]]:
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

    def tail(self, tailNumber:str):
        tailConfig = {}
        for section in self.file.sections():
            if section.lower() == tailNumber.lower():
                tailSection = self.file[section]
                for key in self.file[section]:
                    tailConfig[key] = numberOrString(tailSection[key])
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


class FdrTrackPoint():
    TIME:datetime
    LONG:float
    LAT:float
    ALTMSL:float
    HEADING:float = 0
    PITCH:float = 0
    ROLL:float = 0

    drefs:Dict[str, float] = None

    def __init__(self):
        self.drefs = {}


class FdrFlight():
    ACFT:str = ''
    TAIL:str = ''
    DATE:date = datetime.today()
    PRES:float = 0
    DISA:int = 0
    WIND:Tuple[int, int] = (0,0)

    timezone:int = 0
    track:List[FdrTrackPoint] = None


    def __init__(self):
        self.track = []
        self.summary = ''


class FlightMeta():
    Pilot:str = None
    TailNumber:str = None
    DerivedOrigin:str = None
    StartLatitude:float = None
    StartLongitude:float = None
    DerivedDestination:str = None
    EndLatitude:float = None
    EndLongitude:float = None
    StartTime:float = None
    EndTime:float = None
    TotalDuration:timedelta = None
    TotalDistance:float = None
    InitialAttitudeSource:str = None
    DeviceModel:str = None
    DeviceDetails:str = None
    DeviceVersion:str = None
    BatteryLevel:float = None
    BatteryState:str = None
    GPSSource:str = None
    MaximumVerticalError:float = None
    MinimumVerticalError:float = None
    AverageVerticalError:float = None
    MaximumHorizontalError:float = None
    MinimumHorizontalError:float = None
    AverageHorizontalError:float = None
    ImportedFrom:str = None
    RouteWaypoints:str = None


def main(argv:List[str]):
    parser = argparse.ArgumentParser(
        description='Convert ForeFlight compatible track files into X-Plane compatible FDR files',
        epilog='Example: python 42fdr.py tracklog-1.csv tracklog-2.kml'
    )

    parser.add_argument('-a', '--aircraft', default=None, help='Path to default X-Plane aircraft')
    parser.add_argument('-c', '--config', default=None, help='Path to 42fdr config file')
    parser.add_argument('-t', '--timezone', default=None, help='An offset to add to all times processed.  +/-hh:mm[:ss] or +/-<decimal hours>')
    parser.add_argument('-o', '--outputFolder', default=None, help='Path to write X-Plane compatible FDR v4 output file')
    parser.add_argument('trackfile', default=None, nargs='+', help='Path to one or more ForeFlight compatible track files (CSV, KML)')
    args = parser.parse_args()
    
    config = Config(args)
    for inPath in args.trackfile:
        trackFile = open(inPath, 'r')
        fdrFlight = parseInputFile(config, trackFile, close=True)
        outPath = getOutpath(config, inPath, fdrFlight)
        fdrFile = open(outPath, 'w')
        writeOutputFile(config, fdrFile, fdrFlight)


def parseInputFile(config:Config, trackFile:TextIO, close:bool = False) -> FdrFlight:
    filetype = getFiletype(trackFile)

    if filetype == FileType.CSV:
        return parseCsvFile(config, trackFile)
    elif filetype == FileType.KML:
        return parseKmlFile(config, trackFile)
    elif filetype == FileType.GPX:
        return parseGpxFile(config, trackFile)

    if close and not trackFile.closed:
        trackFile.close


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

    csvReader = csv.reader(trackFile, delimiter=',', quotechar='"')
    metaCols = readCsvRow(csvReader)
    metaCols.remove('Battery State') # Bug in ForeFlight
    metaVals = readCsvRow(csvReader)

    fdrFlight.timezone = config.timezoneCSV if config.timezoneCSV is not None else config.timezone

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

    fdrFlight.summary = flightSummary(flightMeta)

    drefSources, _ = config.drefsByTail(fdrFlight.TAIL)
    tailConfig = config.tail(fdrFlight.TAIL)
    trackCols = readCsvRow(csvReader)
    trackVals = readCsvRow(csvReader)
    while trackVals:
        fdrPoint = FdrTrackPoint()
        
        trackData = dict(zip(trackCols, trackVals))
        fdrPoint.TIME = datetime.fromtimestamp(float(trackData['Timestamp']) + fdrFlight.timezone)
        fdrPoint.LAT = round(float(trackData['Latitude']), 9)
        fdrPoint.LONG = round(float(trackData['Longitude']), 9)
        fdrPoint.ALTMSL = round(float(trackData['Altitude']), 4)
        fdrPoint.HEADING = round(wrapHeading(float(trackData['Course']) + tailConfig['headingtrim']), 3)
        fdrPoint.PITCH = round(wrapAttitude(float(trackData['Pitch']) + tailConfig['pitchtrim']), 3)
        fdrPoint.ROLL = round(wrapAttitude(float(trackData['Bank']) + tailConfig['rolltrim']), 3)

        for name in drefSources:
            value = drefSources[name]
            meta = vars(flightMeta)
            point = vars(fdrPoint)
            fdrPoint.drefs[name] = eval(value.format(**meta, **point, **trackData))

        fdrFlight.track.append(fdrPoint)
        trackVals = readCsvRow(csvReader)
    
    return fdrFlight


def readCsvRow(csvFile) -> List[str]:
    reader = None;
    try:
        reader = next(csvFile)
    finally:
        return reader


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
                # Parse DerivedOrigin / DerivedDestination from flightTitle
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
    fdrFlight.DATE = datetime.today().date()

    tailConfig = config.tail(fdrFlight.TAIL)
    drefSources, _ = config.drefsByTail(fdrFlight.TAIL)

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

    fdrFlight.timezone = config.timezoneKML if config.timezoneKML is not None else config.timezone

    track = trackPlacemark.find("gx:Track", ns)
    times = [datetime.fromisoformat(when.text.replace("Z", "+00:00"))
             for when in track.findall("kml:when", ns)]
    coords = [list(map(float, c.text.strip().split())) for c in track.findall("gx:coord", ns)]

    # Read optional arrays (e.g. pitch, bank, course, speed)
    extras = {}
    for arr in extended.findall(".//gx:SimpleArrayData", ns):
        key = arr.attrib.get("name")
        values = [float(v.text) for v in arr.findall("gx:value", ns)]
        extras[key] = values

    for i, (time, coord) in enumerate(zip(times, coords)):
        trackData = {
            'Timestamp': time.timestamp(),
            'Latitude': coord[1],
            'Longitude': coord[0],
            'Altitude': coord[2] * 3.280839895,
            'Course': extras.get("course", [0])[i],
            'Pitch': extras.get("pitch", [0])[i],
            'Bank': extras.get("bank", [0])[i],
            'Speed': extras.get("speed_kts", [0])[i],
        }

        fdrPoint = FdrTrackPoint()
        fdrPoint.TIME = time + timedelta(seconds=fdrFlight.timezone)
        fdrPoint.LAT = round(trackData['Latitude'], 9)
        fdrPoint.LONG = round(trackData['Longitude'], 9)
        fdrPoint.ALTMSL = round(trackData['Altitude'], 4)
        fdrPoint.HEADING = round(wrapHeading(trackData['Course'] + tailConfig["headingtrim"]), 3)
        fdrPoint.PITCH = round(wrapAttitude(trackData['Pitch'] + tailConfig["pitchtrim"]), 3)
        fdrPoint.ROLL = round(wrapAttitude(trackData['Bank'] + tailConfig["rolltrim"]), 3)

        for name in drefSources:
            expr = drefSources[name]
            meta = vars(flightMeta)
            point = vars(fdrPoint)
            fdrPoint.drefs[name] = eval(expr.format(**meta, **point, **trackData))

        fdrFlight.track.append(fdrPoint)
    
    # Derive key metadata from track and Data block
    flightMeta.StartTime      = fdrFlight.track[0].TIME
    flightMeta.StartLatitude  = fdrFlight.track[0].LAT
    flightMeta.StartLongitude = fdrFlight.track[0].LONG
    flightMeta.EndTime        = fdrFlight.track[-1].TIME
    flightMeta.EndLatitude    = fdrFlight.track[-1].LAT
    flightMeta.EndLongitude   = fdrFlight.track[-1].LONG
    flightMeta.TotalDuration  = flightMeta.EndTime - flightMeta.StartTime

    fdrFlight.DATE = flightMeta.StartTime.date()
    fdrFlight.summary = flightSummary(flightMeta)

    return fdrFlight


def parseGpxFile(config:Config, trackFile:TextIO) -> FdrFlight:
    # gpx = ET.fromstringlist(trackFile.readlines())
    raise NotImplementedError


def flightSummary(flightMeta:FlightMeta) -> str:
    pilot = f' by {flightMeta.Pilot}' if flightMeta.Pilot else ''
    distance = f" {flightMeta.TotalDistance:.2f} miles" if flightMeta.TotalDistance else ""
    hoursMinutes = str(flightMeta.TotalDuration).split(':')[:2]
    origin = flightMeta.DerivedOrigin or "N/A"
    destination = flightMeta.DerivedDestination or "N/A"
    waypoints = flightMeta.RouteWaypoints or "N/A"

    clientLine = ''
    deviceInfo = flightMeta.DeviceDetails or flightMeta.DeviceModel
    if deviceInfo:
        clientLine = f"\n  Client: {deviceInfo}"
        if flightMeta.DeviceVersion:
            clientLine += f" iOS v{flightMeta.DeviceVersion}"

    importedLine = ''
    if flightMeta.ImportedFrom and flightMeta.ImportedFrom != 'iOS':
        importedLine = f"\nImported: {flightMeta.ImportedFrom}"

    heading = f"{flightMeta.TailNumber} - {toYMD(flightMeta.StartTime)}{distance}{pilot} ({hoursMinutes[0]} hours and {hoursMinutes[1]} minutes)"
    underline = '\n'+ ('-' * len(heading))

    return f'''{heading}{underline}
    From: {toHM(flightMeta.StartTime)}Z {origin} ({flightMeta.StartLatitude}, {flightMeta.StartLongitude})
      To: {toHM(flightMeta.EndTime)}Z {destination} ({flightMeta.EndLatitude}, {flightMeta.EndLongitude})
 Planned: {waypoints}
GPS/AHRS: {flightMeta.GPSSource}''' + clientLine + importedLine


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
        fdrComment(fdrFlight.summary),
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
        time    = point.TIME.strftime('%H:%M:%S.%f')
        long    = str.rjust(str(point.LONG), FdrColumnWidth)
        lat     = str.rjust(str(point.LAT), FdrColumnWidth)
        altMSL  = str.rjust(str(point.ALTMSL), FdrColumnWidth)
        heading = str.rjust(str(point.HEADING), FdrColumnWidth)
        pitch   = str.rjust(str(point.PITCH), FdrColumnWidth)
        roll    = str.rjust(str(point.ROLL), FdrColumnWidth)
        fdrFile.write(f'{time}, {long}, {lat}, {altMSL}, {heading}, {pitch}, {roll}')

        drefValues = []
        for dref in drefSources:
            drefValues.append(str.rjust(str(point.drefs[dref]), FdrColumnWidth))
        fdrFile.write(', '+ ', '.join(drefValues) +'\n')


def fdrComment(comment:str):
    return 'COMM, '+ '\nCOMM, '.join(comment.splitlines()) +'\n'


def fdrDrefs(drefDefines:List[str]):
    return 'DREF, ' + '\nDREF, '.join(drefDefines) +'\n'


def fdrColNames(drefNames:List[str]):
    names = '''COMM,                        degrees,             degrees,              ft msl,                 deg,                 deg,                 deg
COMM,                      Longitude,            Latitude,              AltMSL,             Heading,               Pitch,                Roll'''

    for drefName in drefNames:
        names += ', '+ str.rjust(drefName, FdrColumnWidth)

    return names +'\n'


def getOutpath(config:Config, inPath:str, fdrFlight:FdrFlight):
    filename = os.path.basename(inPath)
    outPath = config.outPath or '.'
    return Path(os.path.join(outPath, filename)).with_suffix('.fdr')


def secondsFromString(timezone:str):
    seconds = 0

    timezone = numberOrString(timezone)
    if isinstance(timezone, (float, int)):
        seconds = timezone * 3600
    elif isinstance(timezone, str):
        indexAfterSign = int(timezone[0] in ['+','-'])
        zone = timezone[indexAfterSign:].split(':')

        seconds = float(zone.pop())
        seconds += float(zone.pop()) * 60
        if len(zone):
            seconds += float(zone.pop()) * 3600
        else:
            seconds *= 60

        seconds *= -1 if timezone[0] == '-' else 1

    return seconds


def numberOrString(numeric:str):
    if re.sub('^[+-]', '', re.sub('\\.', '', numeric)).isnumeric():
        return float(numeric)
    else:
        return numeric


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


def toMDY(time:Union[datetime,int,str]):
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