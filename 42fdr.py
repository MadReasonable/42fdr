#!/usr/bin/env python3
import argparse, configparser, csv, os, re, sys
import math
import xml.etree.ElementTree as ET
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
    aircraft:str = 'Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP_G1000.acf'
    config:MutableMapping = None
    drefSources:Dict[str, str] = {}
    drefDefines:List[str] = []

    def __init__(self, cliArgs:argparse.Namespace):
        configFile = self.findConfigFile(cliArgs.config)
        self.config = configparser.RawConfigParser()
        self.config.read(configFile)

        defaults = self.config['Defaults'] if 'Defaults' in self.config else {}
        if cliArgs.aircraft:
            self.aircraft = cliArgs.aircraft
        elif 'aircraft' in defaults:
            self.aircraft = defaults['aircraft']
        if cliArgs.outputFolder:
            self.outPath = cliArgs.outputFolder
        elif 'outpath' in defaults:
            self.outPath = defaults['outpath']


        self.addDref('sim/cockpit2/gauges/indicators/ground_speed_kt', '{Speed}', '1.0', 'GndSpd')
        if 'DREFS' in self.config.sections():
            drefs = self.config['DREFS']
            for dref in drefs:
                self.addDref(dref, *[x.strip() for x in drefs[dref].rsplit(',', 3)])

    def addDref(self, instrument:str, value:str, scale:str='1.0', name:str=None):
        name = name or instrument.rpartition('/')[2][:FdrColumnWidth]
        if name not in self.drefSources:
            self.drefSources[name] = value
            self.drefDefines.append(f'{instrument}\t{scale}\t\t// source: {value}')

    def acftByTail(self, tailNumber:str):
        for section in self.config.sections():
            if section.lower().startswith('aircraft/'):
                aircraft = self.config[section]
                if tailNumber in [tail.strip() for tail in aircraft['Tails'].split(',')]:
                    return section

    def tail(self, tailNumber:str):
        for section in self.config.sections():
            if section.lower() == tailNumber.lower():
                tailSection = self.config[section]

                tailConfig = {}
                for key in self.config[section]:
                    tailConfig[key] = numberOrString(tailSection[key])

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
            flightMeta.StartLatitude = float(colValue)
        elif colName == 'Start Longitude':
            flightMeta.StartLongitude = float(colValue)
        elif colName == 'Derived Destination':
            flightMeta.DerivedDestination = colValue
        elif colName == 'End Latitude':
            flightMeta.EndLatitude = float(colValue)
        elif colName == 'End Longitude':
            flightMeta.EndLongitude = float(colValue)
        elif colName == 'Start Time':
            flightMeta.StartTime = datetime.fromtimestamp(float(colValue) / 1000)
        elif colName == 'End Time':
            flightMeta.EndTime = datetime.fromtimestamp(float(colValue) / 1000)
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

    tailConfig = config.tail(fdrFlight.TAIL)
    trackCols = readCsvRow(csvReader)
    trackVals = readCsvRow(csvReader)
    while trackVals:
        fdrPoint = FdrTrackPoint()
        
        trackData = dict(zip(trackCols, trackVals))
        fdrPoint.TIME = datetime.fromtimestamp(float(trackData['Timestamp']));
        fdrPoint.LAT = float(trackData['Latitude'])
        fdrPoint.LONG = float(trackData['Longitude'])
        fdrPoint.ALTMSL = float(trackData['Altitude'])
        fdrPoint.HEADING = plusMinus180(float(trackData['Course']) + tailConfig['headingtrim'])
        fdrPoint.PITCH = plusMinus180(float(trackData['Pitch']) + tailConfig['pitchtrim'])
        fdrPoint.ROLL = plusMinus180(float(trackData['Bank']) + tailConfig['rolltrim'])

        for name in config.drefSources:
            value = config.drefSources[name]
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

    return f'''{flightMeta.TailNumber} - {toMDY(flightMeta.StartTime)} {flightMeta.TotalDistance} miles{(' by '+ flightMeta.Pilot) if flightMeta.Pilot else ''} ({hoursMinutes[0]} hours and {hoursMinutes[1]} minutes)

    From: {toHMS(flightMeta.StartTime)} {flightMeta.DerivedOrigin} ({flightMeta.StartLatitude}, {flightMeta.StartLongitude})
      To: {toHMS(flightMeta.EndTime)} {flightMeta.DerivedDestination} ({flightMeta.EndLatitude}, {flightMeta.EndLongitude})
 Planned: {flightMeta.RouteWaypoints}
GPS/AHRS: {flightMeta.GPSSource}
  Client: {flightMeta.DeviceModelDetailed} iOS v{flightMeta.iOSVersion}'''


def writeOutputFile(config:Config, fdrFile:TextIO, fdrData:FdrFlight):
    timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d %H:%M:%SZ')

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
        fdrDrefs(config.drefDefines),
        '\n\n',
        fdrComment('The remainder of this file consists of GPS/AHRS track points.'),
        fdrComment('The timestamps beginning each row are in the same timezone as the original file.'),
        '\n',
        fdrColNames(config.drefSources.keys()),
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
        for dref in config.drefSources:
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


def numberOrString(numeric:str):
    if re.sub('^-', '', re.sub('\\.', '', numeric)).isnumeric():
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


def getOutpath(config:Config, inPath:str, fdrData:FdrFlight):
    filename = os.path.basename(inPath)
    outPath = config.outPath or '.'
    return Path(os.path.join(outPath, filename)).with_suffix('.fdr')


def toMDY(time:Union[datetime,int,str]):
    if isinstance(time, str):
        time = int(time)
    if isinstance(time, int):
        time = datetime.fromtimestamp(time / 1000)
    return time.strftime('%m/%d/%Y')


def toHMS(time:Union[datetime,int,str]):
    if isinstance(time, str):
        time = int(time)
    if isinstance(time, int):
        time = datetime.fromtimestamp(time / 1000)
    return time.strftime('%H:%M:%S')


if __name__ == '__main__':
    sys.exit(main(sys.argv))