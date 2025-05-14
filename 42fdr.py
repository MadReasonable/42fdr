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
    outPath:str = '.'
    timezone:int = 0
    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf'

    file:MutableMapping = None

    def __init__(self, cliArgs:argparse.Namespace):
        self.file = configparser.RawConfigParser()
        configFile = self.findConfigFile(cliArgs.config)
        if configFile:
            self.file.read(configFile)

        defaults = self.file['Defaults'] if 'Defaults' in self.file else {}

        if cliArgs.aircraft:
            self.aircraft = cliArgs.aircraft
        elif 'aircraft' in defaults:
            self.aircraft = defaults['aircraft']

        if cliArgs.timezone:
            self.timezone = secondsFromString(cliArgs.timezone)
        elif 'timezone' in defaults:
            self.timezone = secondsFromString(defaults['timezone'])

        if cliArgs.outputFolder:
            self.outPath = cliArgs.outputFolder
        elif 'outpath' in defaults:
            self.outPath = defaults['outpath']

    def acftByTail(self, tailNumber:str):
        for section in self.file.sections():
            if section.lower().startswith('aircraft/'):
                aircraft = self.file[section]
                if tailNumber in [tail.strip() for tail in aircraft['Tails'].split(',')]:
                    return section

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

            instrument = key[5:].strip()

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
    DeviceModelDetailed:str = None
    iOSVersion:str = None
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
    parser = argparse.ArgumentParser(description='Convert ForeFlight compatible track files into X-Plane compatible FDR files')
    parser.add_argument('-a', '--aircraft', default=None, help='Path to default X-Plane aircraft')
    parser.add_argument('-c', '--config', default=None, help='Path to 42fdr config file')
    parser.add_argument('-t', '--timezone', default=None, help='An offset to add to all times processed.  +/-hh:mm[:ss] or +/-<decimal hours>')
    parser.add_argument('-o', '--outputFolder', default=None, help='Path to write X-Plane compatible FDR v4 output file')
    parser.add_argument('trackfile', default=None, nargs='+', help='Path to one or more ForeFlight compatible track files (CSV)')
    args = parser.parse_args()
    
    config = Config(args)
    for inPath in args.trackfile:
        trackFile = open(inPath, 'r')
        fdrData = parseInputFile(config, trackFile, close=True)
        outPath = getOutpath(config, inPath, fdrData)
        fdrFile = open(outPath, 'w')
        writeOutputFile(config, fdrFile, fdrData)


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
        if line.startsWith('<kml'):
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
            flightMeta.StartTime = datetime.fromtimestamp(float(colValue) / 1000 + config.timezone)
            fdrFlight.DATE = flightMeta.StartTime.date()
        elif colName == 'End Time':
            flightMeta.EndTime = datetime.fromtimestamp(float(colValue) / 1000 + config.timezone)
        elif colName == 'Total Duration':
            flightMeta.TotalDuration = timedelta(seconds=float(colValue))
        elif colName == 'Total Distance':
            flightMeta.TotalDistance = float(colValue)
        elif colName == 'Initial Attitude Source':
            flightMeta.InitialAttitudeSource = colValue
        elif colName == 'Device Model':
            flightMeta.DeviceModel = colValue
        elif colName == 'Device Model Detailed':
            flightMeta.DeviceModelDetailed = colValue
        elif colName == 'iOS Version':
            flightMeta.iOSVersion = colValue
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
        fdrPoint.TIME = datetime.fromtimestamp(float(trackData['Timestamp']) + config.timezone)
        fdrPoint.LAT = round(float(trackData['Latitude']), 9)
        fdrPoint.LONG = round(float(trackData['Longitude']), 9)
        fdrPoint.ALTMSL = round(float(trackData['Altitude']), 4)
        fdrPoint.HEADING = round(plusMinus180(float(trackData['Course']) + tailConfig['headingtrim']), 3)
        fdrPoint.PITCH = round(plusMinus180(float(trackData['Pitch']) + tailConfig['pitchtrim']), 3)
        fdrPoint.ROLL = round(plusMinus180(float(trackData['Bank']) + tailConfig['rolltrim']), 3)

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


def parseKmlFile(config:Config, trackFile:TextIO) -> FdrFlight:
    # kml = ET.fromstringlist(trackFile.readlines())
    raise NotImplementedError


def parseGpxFile(config:Config, trackFile:TextIO) -> FdrFlight:
    # gpx = ET.fromstringlist(trackFile.readlines())
    raise NotImplementedError


def flightSummary(flightMeta:FlightMeta) -> str:
    hoursMinutes = str(flightMeta.TotalDuration).split(':')[:2]

    return f'''{flightMeta.TailNumber} - {toYMD(flightMeta.StartTime)} {flightMeta.TotalDistance:.2f} miles{(' by '+ flightMeta.Pilot) if flightMeta.Pilot else ''} ({hoursMinutes[0]} hours and {hoursMinutes[1]} minutes)

    From: {toHM(flightMeta.StartTime)}Z {flightMeta.DerivedOrigin} ({flightMeta.StartLatitude}, {flightMeta.StartLongitude})
      To: {toHM(flightMeta.EndTime)}Z {flightMeta.DerivedDestination} ({flightMeta.EndLatitude}, {flightMeta.EndLongitude})
 Planned: {flightMeta.RouteWaypoints}
GPS/AHRS: {flightMeta.GPSSource}
  Client: {flightMeta.DeviceModelDetailed} iOS v{flightMeta.iOSVersion}'''


def writeOutputFile(config:Config, fdrFile:TextIO, fdrData:FdrFlight):
    timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M:%SZ')
    drefSources, drefDefines = config.drefsByTail(fdrData.TAIL)

    fdrFile.writelines([
        'A\n4\n',
        '\n',
        fdrComment(f'Generated on [{timestamp}]'),
        fdrComment(f'This X-Plane compatible FDR file was converted from a ForeFlight track file using 42fdr.py'),
        fdrComment('https://github.com/MadReasonable/42fdr'),
        '\n',
        fdrComment(fdrData.summary),
        '\n\n',
        fdrComment("Fields below define general data for this flight."),
        fdrComment("ForeFlight only provides a few of the data points that X-Plane can accept.") ,
        '\n',
        f'ACFT, {config.acftByTail(fdrData.TAIL) or config.aircraft}\n',
        f'TAIL, {fdrData.TAIL}\n',
        f'DATE, {toMDY(fdrData.DATE)}\n',
        '\n\n',
        fdrComment('DREFs below (if any) define additional columns beyond the 7th (Roll)'),
        fdrComment('in the flight track data that follows.'),
        '\n',
        fdrDrefs(drefDefines),
        '\n\n',
        fdrComment('The remainder of this file consists of GPS/AHRS track points.'),
        fdrComment('The timestamps beginning each row are in the same timezone as the original file.'),
        '\n',
        fdrColNames(drefSources.keys()),
    ])

    for point in fdrData.track:
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


def getOutpath(config:Config, inPath:str, fdrData:FdrFlight):
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


def plusMinus180(degrees:float):
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
    sys.exit(main(sys.argv))