# 42fdr
Python script to convert ForeFlight's exported flight tracks to X-Plane compatible FDR files

*\*Only works with CSV files currently*


## Installation
Just put 42fdr.py somewhere on your computer.
- Works with python 3.9 and above.
- Single file with no 3rd-party dependencies.


## Usage
`[python3] 42fdr.py [-c configFile] [-a aircraft] [-t timezone] [-o outputFolder] trackFile1 [trackFile2, trackFile3, ...]`

**You should be able to run 42fdr without explicitly invoking the python interpreter.*

42FDR will convert one or more files, rename it with the `.fdr` extension, and save the output to the current working directory.

| Options | Description |
|---------|-------------|
| `-c`    | Specify a config file.  A config file can be used to set the options below instead of on the command-line.  A config file can also define custom DREFs, automatically lookup an X-Plane aircraft by tail number, and load tail specific attitude calibrations.
| `-a`    | Choose an X-Plane aircraft.  X-Plane requires the FDR file to specify an aircraft model for the flight and this is not provided by the ForeFlight track file.  `Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf` is used by default unless overriden with a config file or command line option.
| `-t`    | Adjust all times by this (positive or negative) amount.  If you've recorded your flight in local time, this value should be the *opposite* of your actual timezone. It will be added to recorded timestamps to get Zulu time.  Can be expressed as a decimal number of hours `(e.g. 3.5)` or in the format +/-hh:mm[:ss] `(e.g. -5:00)`
| `-o`    | Choose a different output path.



## Using a config file
Config files are optional.
They can be used to avoid passing long parameters on the command line, to compute additional columns for replay, to automatically load the correct X-Plane model for the recorded tail number, and to correct for heading/pitch/roll deviations in specific aircraft.

42fdr will automatically search for a config file named either 42fdr.conf or 42fdr.ini, first in the folder from where you run 42fdr.py and then in the folder where 42fdr.py lives.
You can also specify a custom config file location from the command line.

An example configuration is provided with the name `42fdr.conf.example`.
Make a copy or rename it, then edit it as needed.
One `[Defaults]` section and as many `[<Aircraft/*>]` and `[<Tail>]` sections as needed are supported.


### DREF Definitions
The required, default fields in an X-Plane FDR file only include time, position, attitude.
This is fine for getting your simulated plane to follow the track but the cockpit will be dead.
To get the instruments like the airspeed indicator and artificial horizon working, additional fields must be added to the FDR file to provide the appropriate values.

The `DREF` keys allow you to add additional fields to the output FDR file.
ForeFlight only provides basic position, attitude, and ground speed. This feature can be used to copy those values to additional fields `(e.g. ground speed to airspeed indicator)`, to pass constant values `(e.g. 29.92)`, and to compute new values `(e.g. round({Pitch}, 3))`.

You can define custom DREFs in any section:

- `[Defaults]` — applies to all flights
- `[Aircraft/...]` — applies to flights using that aircraft model
- `[Tail]` — applies to flights from that specific tail number
 
Each DREF key must begin with `DREF` followed by the dataref path. This path is the name of the X-Plane dataref to set, and must match exactly (e.g. `sim/cockpit2/gauges/indicators/airspeed_kts_pilot`):
```
DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = {Speed}, 1.0, IAS
```

The value supports three fields:
```
<expression>, <scale>, <optionalColumnName>
```

Where:
- `<expression>` is a Python expression using values from the track or metadata
- `<scale>` is a number (usually 1.0) that is kept for legacy reasons
- `<optionalColumnName>` (optional) overrides the auto-generated column header

Examples:
```
DREF sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot = 29.92
DREF sim/cockpit2/gauges/indicators/altitude_ft_pilot = round({ALTMSL}, 2), 1.0, Altimeter
DREF sim/cockpit2/gauges/indicators/compass_heading_deg_mag = {HEADING}, Compass
```


### [Defaults]
The `Defaults` section supports three keys: `aircraft`, `timezone`, and `outpath`, which provide defaults for when their respective command-line arguments are not provided.

DREFs defined in this section will be included in **all** generated FDR files.


### [<Aircraft/*>]
`<Aircraft/*>` sections allow you to map specific tail numbers to X-Plane aircraft models.
The section name should be the path to the .acf model file, beginning with the Aircraft folder `(e.g. [Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf])`

A single key is supported, `Tails`, which can be used to list all tail numbers which should cause this aircraft to be used in the output file `(e.g. N1234X, N5678Y)`

DREFs defined in this section will be included in FDR files generated for this aircraft model.


### [\<Tail>]
\<Tail> sections allow for correction of attitude information in the flight track.
\<Tail> section names are just airplane registration numbers `(e.g. N1234X)`.

These sections support:
- `headingTrim`, `pitchTrim`, `rollTrim` — These offsets will be added to attitude data for every track point.

DREFs defined in this section will be included in FDR files generated for this specific tail number.

#### 42fdr.conf example:
```
[Defaults]
Aircraft = Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf
Timezone = 5
OutPath  = .

DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = {Speed}, 1.0, IAS
DREF sim/cockpit2/gauges/indicators/altitude_ft_pilot = round({ALTMSL}, 2), 1.0, Altimeter
DREF sim/cockpit2/gauges/indicators/compass_heading_deg_mag = {HEADING}, 1.0, Compass


[Aircraft/PIPERS_1150/Piper_PA-28-161/Piper_PA-28-161(Garmin)/piper warrior.acf]
Tails = N123ND, N321ND

DREF sim/cockpit2/gauges/indicators/heading_vacuum_deg_mag_pilot = round({HEADING}, 3), 1.0, Vacuum Heading
DREF sim/cockpit2/gauges/indicators/pitch_vacuum_deg_pilot = {PITCH}, 1.0, Vacuum Pitch
DREF sim/cockpit2/gauges/indicators/roll_vacuum_deg_pilot = {ROLL}, 1.0, Vacuum Roll
DREF sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot = 29.92, 1.0, Barometer


[N123ND]
headingTrim = 0.03
pitchTrim   = -0.01
rollTrim    = 0.0
```


#### DREF Field Reference:
*\*CSV Track data contains the raw values from the input file.
After converting the timestamp to a normal date and time, adjusting for timezone, and calibrating the attitude, the processed data is made available as FDR Track data*

*\*\*GndSpd is not available in FDR Track data as it is technically a DREF value and not part of the core FDR file*

These are the available placeholders for use in DREF expressions:
- `Track (FDR)` values are computed for replay
- `Track (CSV)` values come from the raw input
- `Flight (meta)` values are metadata or inferred

| Track (FDR) | Track (CSV) | Flight (meta)            |
|-------------|-------------|--------------------------|
| {TIME}      | {Timestamp} | {Pilot}                  |
| {LAT}       | {Latitude}  | {TailNumber}             |
| {LONG}      | {Longitude} | {DerivedOrigin}          |
| {ALTMSL}    | {Altitude}  | {StartLatitude}          |
| {HEADING}   | {Course}    | {StartLongitude}         |
| {PITCH}     | {Pitch}     | {DerivedDestination}     |
| {ROLL}      | {Bank}      | {EndLatitude}            |
|             | {Speed}     | {EndLongitude}           |
|             |             | {StartTime}              |
|             |             | {EndTime}                |
|             |             | {TotalDuration}          |
|             |             | {TotalDistance}          |
|             |             | {InitialAttitudeSource}  |
|             |             | {DeviceModel}            |
|             |             | {DeviceModelDetailed}    |
|             |             | {iOSVersion}             |
|             |             | {BatteryLevel}           |
|             |             | {BatteryState}           |
|             |             | {GPSSource}              |
|             |             | {MaximumVerticalError}   |
|             |             | {MinimumVerticalError}   |
|             |             | {AverageVerticalError}   |
|             |             | {MaximumHorizontalError} |
|             |             | {MinimumHorizontalError} |
|             |             | {AverageHorizontalError} |
|             |             | {ImportedFrom}           |
|             |             | {RouteWaypoints}         |


## Command-line Examples

<b style='font-size:smaller'>`./42fdr.py tracklog-E529A53E-FBC7-4CAC-AB46-28C123A9038A.csv`</b>

The simplest use case.  Python is installed and configured correctly, we are in a bash shell, the script is in the same folder as the track file, we are only converting one file, using the default aircraft, and it should be saved to the current folder.

This will create the file:
- `./tracklog-E529A53E-FBC7-4CAC-AB46-28C123A9038A.fdr`

---
<b style='font-size:smaller'>`python3 42fdr.py -a "Aircraft/Laminar Research/Lancair Evolution/N844X.acf" tracklog-E529A53E.csv`</b>

The same as above, except the Python interpreter is called explicitly, which is needed when using Windows, and the aircraft is changed to the Lancair Evolution.

This will create the file:
- `./tracklog-E529A53E.fdr`

---
<b style='font-size:smaller'>`python3 C:\Users\MadReasonable\bin\42fdr.py -o C:\Users\MadReaonble\Desktop tracklog-E529A53E.csv tracklog-DC7A92F3.csv`</b>

Convert more than one file and send the output to the desktop.
The script is not in the current working directory.

This will create the files:
- `C:\Users\MadReasonable\Desktop\tracklog-E529A53E.fdr`
- `C:\Users\MadReasonable\Desktop\tracklog-DC7A92F3.fdr`

---
<b style='font-size:smaller'>`42fdr.py -c "../mycustom.ini" tracklog-E529A53E.csv`</b>

A custom config file is used.  The FDR files output path, aircraft, and columns depend on the specific configuration.

This will create the file:
- `<path/depends/on/config>/tracklog-E529A53E.fdr`
