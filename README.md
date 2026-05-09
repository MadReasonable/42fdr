# 42fdr
**Python script to convert ForeFlight's exported flight tracks to X-Plane compatible FDR files.**

42fdr is a single Python script, `42fdr.py`, with no additional dependencies or build steps.
A Windows batch file, `42fdr.bat`, is included for convenience and allows the tool to be used like a native command.

It is simple to use out of the box, working without any configuration or command-line options:
```cmd
42fdr trackfile-1.kml [trackfile-2.csv ...]
```

When properly configured, it can process hundreds of flights across a fleet of aircraft — automatically assigning the correct X-Plane model and setting up custom cockpit instruments.

*Requires Python 3.9 or higher*  
<br/>

## Installation

42fdr does not need to be "installed" — just extract the files and add the folder to your PATH.
<br/>


### Step 1 – Install Python (if missing)
---
If you're not sure whether Python is already installed, open a command prompt or terminal and run:

```bash
python --version
```
<br/>

If that doesn't work, or if the version is older than 3.9, download and install the latest version from:

https://www.python.org/downloads/

✅ **Be sure to check “Add Python to PATH” during installation.**  
<br/>

### Step 2 – Download 42fdr
---

1. Get the latest release from GitHub:

   - https://github.com/MadReasonable/42fdr/releases

     Download in your preferred format (`.zip` or `.tar.gz`).

2. Extract the entire folder to a working location.

   It’s recommended to place the folder somewhere in your home directory to avoid needing elevated permissions.

   - **Windows:**
     ```cmd
     %USERPROFILE%\42fdr
     ```
     *(e.g. C:\\Users\\\<yourname>\\42fdr)*

   - **macOS/Linux:**
     ```bash
     ~/.local/42fdr
     ```
     <br/>

### Step 3 – Set Up 42fdr
---
*(Recommended)* Add the folder to your system `PATH` so you can run `42fdr` from any directory:

- **Windows:**
  - Press `Win + R`, type `sysdm.cpl`, and press Enter
  - Go to the **Advanced** tab → click **Environment Variables**
  - Under “User variables,” edit `Path` and add:

    ```cmd
    %USERPROFILE%\42fdr
    ```

- **macOS/Linux:**
  - Add this line to your shell config file (e.g. `~/.bashrc`, `~/.zshrc`, or `~/.profile`):

    ```bash
    export PATH="$HOME/.local/42fdr:$PATH"
    ```

✅ After updating your PATH, close and reopen any terminal windows to apply the change.

<br/>

## Usage
Windows (via *42fdr.bat* in PATH):
```cmd
42fdr [-c configFile] [-a aircraft] [-t aircraftType] [-z timezone] [-o outputFolder] [--airfieldDB] [--airfieldDBPath path] [--inferRoute] [-O offsetOrig] [-D offsetDest] trackFile1 [trackFile2 ...]
```

macOS/Linux:
```bash
42fdr.py [-c configFile] [-a aircraft] [-t aircraftType] [-z timezone] [-o outputFolder] [--airfieldDB] [--airfieldDBPath path] [--inferRoute] [-O offsetOrig] [-D offsetDest] trackFile1 [trackFile2 ...]
```

> **Breaking changes:** `-t` now selects `--aircraftType` and the timezone short flag has moved to `-z`.

42fdr converts one or more files, renames them with the `.fdr` extension, and saves the output to the current working directory.

42fdr supports both CSV and KML files, but CSV are preferred as they provide more metadata in a smaller, simpler file.
Either format will produce equally valid FDR files.


| Options | Description |
|---------|-------------|
| `-c`    | Specify a config file.  A config file can be used to set the options below instead of on the command line.  A config file can also define custom DREFs, automatically look up an X-Plane aircraft by tail number, and load tail-specific attitude calibrations.
| `-a`    | Choose an X-Plane aircraft. X-Plane requires the FDR file to specify an aircraft model, which is not included in the ForeFlight track file. `Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf` is used by default unless overridden by a config file or command-line option.
| `-t`    | Aircraft category: `airplane` (default), `helicopter`, or `balloon`. Controls which OurAirports records are eligible for route detection and airport offsets (e.g. `helicopter` keeps heliports, `balloon` keeps balloonports).
| `-z`    | Adjust all times by this (positive or negative) amount.  If you've recorded your flight in local time, this value should be the *opposite* of your actual timezone. It will be added to recorded timestamps to get Zulu time.  Can be expressed as a decimal number of hours (e.g. `3.5`) or in the format +/-hh:mm[:ss] (e.g. `-5:00`)
| `-o`    | Choose a different output path for the generated `.fdr` files.w
| `--airfieldDB` | Enable airfield lookup from OurAirports data using the default `OurAirports.csv` path in the 42fdr.py script folder. Downloads CSV if missing or out of date.
| `--airfieldDBPath` | Enable airfield lookup using a specific OurAirports CSV file or a directory that contains `OurAirports.csv`.
| `--inferRoute` | Infer actual route from waypoints defined in config and the airfield DB (if enabled).
| `-O`    | Offset in feet at the **origin** airfield: `east,north,up` (e.g. `"2,0,-15.5"`). See below.
| `-D`    | Same for the **destination** airfield. See below.
<br/>

### Offsets: Fixing Hovering During Taxi
---
Recorded GPS/AHRS height and runway elevation in X-Plane often disagree slightly, resulting in wheels floating above during taxi.
When configured, airfield offsets move the aircraft position so ground contact matches the scenery while leaving your instruments as-recorded.
This will not fix positioning errors caused by GPS jitter, *but it can be used to mask it*.
Setting a negative vertical offset greater than the jitter will "glue" your aircraft to the ground during taxi, at the loss of accurate takeoff/landing times and positions.

Offset effects are gradually diminished with distance from configured areas, with no effect when beyond the defined outer radius (6 nautical miles by default).

#### Coordinate Frame
Adjustments are given in feet as 3 numbers separated by commas (**east**, **north**, and **up**).
For example, the command line argument `-O "2,0,-15.5"` moves the aircraft **2 ft east** and **15.5 ft down** at the origin airfield.

#### Waypoints and Airfields
Waypoint offsets are defined in the config file via `[Waypoint <Name>]` sections.
Each section specifies a location using lat/long coordinates and defines an offset using the standard (**east**, **north**, and **up**) notation.

When database lookup is enabled via the `--airfieldDB` or `--airfieldDBPath` command-line options, a CSV file from `https://davidmegginson.github.io/ourairports-data/airports.csv` is used to provide lat/long coordinates missing from matching `[Waypoint <Name>]` sections.
This file is cached for 90 days by default.
Section names are matched by ICAO, IATA, and common identifiers.

Offsets transition smoothly from 100% inside the inner radius to 0% outside the outer radius.
Overlapping airspaces are blended smoothly by distance.
Exact weighting appears under `[Waypoint <Name>] Sections` and airport offset blending later in this file.

##### Inferred Routes
`42fdr` writes flight metadata to comments in the FDR files it generates.
ForeFlight CSV files include planned route waypoints, which may not match the actual flight.
Waypoints from the config file and from the OurAirports airfield database can be used to infer the actual route flown.

#### From the Command-Line
**`-O`** and **`-D`** allow you to specify offsets for the origin and destination airfields from the command line.

They either add onto already matching waypoint offsets, or **`42fdr`** creates new waypoints (e.g. `ORIG`, `DEST`, or `HOME`) centered around the first and/or last track points.

<br/>

## Using a Config File
Config files are optional.
They can be used to avoid passing long parameters on the command line, to compute additional columns for replay, to automatically load the correct X-Plane model for the recorded tail number, and to correct for heading/pitch/roll deviations in specific aircraft.

42fdr automatically searches for a config file named either 42fdr.conf or 42fdr.ini, first in the current working folder and then in the folder where 42fdr.py lives.
You can also specify a custom config file location from the command line.

An example configuration is provided with the name `42fdr.conf.example`.
Make a copy or rename it, then edit it as needed.
One `[Defaults]` section, an optional `[AirfieldDB]`, and as many `[<Aircraft/*>]`, `[Tail <Tail>]`, and `[Waypoint <Name>]` sections as needed are supported.


### 42fdr.conf example:
```ini
[Defaults]
Aircraft    = Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf
Timezone    = 5         ; CSV files record times in Local Time
TimezoneKML = 0         ; KML files record times in UTC
OutPath     = .
inferRoute

DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = {Speed}, 1.0, IAS
DREF sim/cockpit2/gauges/indicators/altitude_ft_pilot = round({ALTMSL}, 2), 1.0, Altimeter
DREF sim/cockpit2/gauges/indicators/compass_heading_deg_mag = {HEADING}, 1.0, Compass


[Aircraft/PIPERS_1150/Piper_PA-28-161/Piper_PA-28-161(Garmin)/piper warrior.acf]
Tails = N123ND, N321ND

DREF sim/cockpit2/gauges/indicators/heading_vacuum_deg_mag_pilot = round({HEADING}, 3), 1.0, Vacuum Heading
DREF sim/cockpit2/gauges/indicators/pitch_vacuum_deg_pilot = {PITCH}, 1.0, Vacuum Pitch
DREF sim/cockpit2/gauges/indicators/roll_vacuum_deg_pilot = {ROLL}, 1.0, Vacuum Roll
DREF sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot = 29.92, 1.0, Barometer


[Tail N123ND]
headingTrim = 0.03
pitchTrim   = -0.01
rollTrim    = 0.0


[AirfieldDB]
enabled = true
MaxAgeDays = 120
Path = ./OurAirports.csv


[Waypoint KJFK]
lat = 40.6413
lon = -73.7781
offset = 8.0,-2.5,4.0
innerRadiusNm = 2.0
outerRadiusNm = 8.0
```
<br/>

### DREF Definitions
---
The required, default fields in an X-Plane FDR file only include time, position, attitude.
This is enough to make your simulated aircraft follow the track, but cockpit instruments won’t function correctly without additional data.
To get instruments like the airspeed indicator and artificial horizon working, additional fields must be added to the FDR file to provide the appropriate values.

`DREF` keys allow you to add additional fields to the output FDR file.
ForeFlight only provides basic position, attitude, and ground speed. This feature can be used to copy those values to additional fields `(e.g. ground speed to airspeed indicator)`, to pass constant values `(e.g. 29.92)`, and to compute new values `(e.g. round({Pitch}, 3))`.

You can define custom DREFs in the following sections:
- `[Defaults]` — applies to all flights
- `[Aircraft/...]` — applies to flights using that aircraft model
- `[Tail <Tail>]` — applies to flights from that specific tail number

<br/> 

Each DREF key must begin with `DREF` followed by the dataref path.
```ini
DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = {Speed}, 1.0, IAS
```
*\*A searchable list of all available datarefs is available at https://developer.x-plane.com/datarefs/*

<br/>

The value supports three fields:
```ini
<expression>, <scale>, <optionalColumnName>
```
<br/>

Where:
- `<expression>` is a Python expression using values from the track or metadata
- `<scale>` is a number (usually 1.0) that is kept for legacy reasons
- `<optionalColumnName>` (optional) overrides the auto-generated column header

<br/>


### [Defaults] Section
---
The `[Defaults]` section defines fallback values used when command-line options are not provided. Common keys include:
- `aircraft` – default X-Plane aircraft path
- `AircraftType` – default aircraft category for OurAirports filtering when using `--airfieldDB`: `airplane` (default), `helicopter`, or `balloon`. Overridden by `-t` on the command line. See the **[AirfieldDB]** section for how categories map to airfield types.
- `inferRoute` – Whether to derive the flight route from configured waypoints and the OurAirports database (`--airfieldDB`). Off unless you add the key, set `inferRoute = true`, or pass `--inferRoute`.
- `timezone` – default timezone offset
- `outpath` – default folder for generated `.fdr` files

<br/>

You can also specify `timezoneCSV` and `timezoneKML` to override `timezone` for those specific input file types. However, the `--timezone` command-line option always takes precedence over all of these.

DREFs defined in this section will be included in **all** generated FDR files.  
<br/>

### [<Aircraft/*>] Sections
---
`<Aircraft/*>` sections allow you to map specific tail numbers to X-Plane aircraft models.
The section name should be the path to the .acf model file, beginning with the Aircraft folder. For example:
```ini
[Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf]
```

The primary key is `Tails`, used to list all tail numbers for which this `.acf` model should be selected in the output file `(e.g. N1234X, N5678Y)`.

Optional **`AircraftType`** — same values as in `[Defaults]` / `-t` (`airplane`, `helicopter`, `balloon`). Used for OurAirports filtering and route detection when using `--airfieldDB`. See **[AirfieldDB]** for how categories map to airfield types.

DREFs defined in this section will be included in FDR files generated for this aircraft model.  
<br/>

### [Tail \<Tail>] Sections
---
`[Tail <Tail>]` sections allow correction of attitude information in the flight track.
Use the aircraft registration after `Tail` in the section name `(e.g. [Tail N1234X])`.
Legacy `[<Tail>]` sections without the `Tail` prefix are still supported for backward compatibility.

These sections support:
- `headingTrim`, `pitchTrim`, `rollTrim` — These offsets will be added to attitude data for every track point.

DREFs defined in this section will be included in FDR files generated for this specific tail number.

<br/>

### [AirfieldDB] Section
---
The optional `[AirfieldDB]` section controls how the OurAirports database is used.
Set `enabled` to turn it on from config.

`Path` can point to a CSV file or to a directory containing `OurAirports.csv`.
`MaxAgeDays` controls how old the local file can be before 42fdr tries to refresh it (default is 90 days).

Visit radii are used to control route detection sensitivity for the `inferRoute` feature.
These settings do not affect waypoint inner/outer offset blending.  OurAirports airfield types are controlled by:
- `LargeAirportVisitRadius`
- `MediumAirportVisitRadius`
- `SmallAirportVisitRadius`
- `SeaplaneBaseVisitRadius`
- `HeliportVisitRadius`
- `BalloonportVisitRadius`
- `DefaultVisitRadius` — unexpected types

<br/>

### [Waypoint \<Name>] Sections
---
`[Waypoint <Name>]` sections define position-based replay offsets that can be used for airports or any custom location.

These sections support:
- `offset` — offset in feet: `east,north,up`.  Required unless `hideFromRoute` is enabled.
- `lat`, `lon` — waypoint center in decimal degrees.  Required unless `airfieldDB` or `hideFromRoute` is enabled.
- `innerRadiusNm`, `outerRadiusNm` (optional) — offset blending radii in nautical miles (defaults are 2 and 6)
- `hideFromRoute` (optional) — keep this waypoint out of the derived route summary. Off unless you add the key, or set `hideFromRoute = true` / `false` explicitly.

<br/>

## DREF Field Reference:
*\*Raw Track data contains the raw values from the input file.
After converting the timestamp to a normal date and time, adjusting for timezone, and calibrating the attitude, the processed data is made available as FDR Track data*

*\*\*GndSpd is not available in FDR Track data as it is technically a DREF value and not part of the core FDR file*

These are the available placeholders for use in DREF expressions:
- `Track (FDR)` values are computed for replay
- `Track (CSV)` values come from the raw input
- `Flight (meta)` values are metadata or inferred

| Flight (meta)            | Track (raw) | Track (FDR) |
|--------------------------|-------------|-------------|
| {Pilot}                  | {Timestamp} | {TIME}      |
| {TailNumber}             | {Latitude}  | {LAT}       |
| {DerivedOrigin}          | {Longitude} | {LONG}      |
| {StartLatitude}          | {Altitude}  | {ALTMSL}    |
| {StartLongitude}         | {Course}    | {HEADING}   |
| {DerivedDestination}     | {Pitch}     | {PITCH}     |
| {EndLatitude}            | {Bank}      | {ROLL}      |
| {EndLongitude}           | {Speed}     |             |
| {StartTime}              |             |             |
| {EndTime}                |             |             |
| {TotalDuration}          |             |             |
| {TotalDistance}          |             |             |
| {InitialAttitudeSource}  |             |             |
| {DeviceModel}            |             |             |
| {DeviceDetails}          |             |             |
| {DeviceVersion}          |             |             |
| {BatteryLevel}           |             |             |
| {BatteryState}           |             |             |
| {GPSSource}              |             |             |
| {MaximumVerticalError}   |             |             |
| {MinimumVerticalError}   |             |             |
| {AverageVerticalError}   |             |             |
| {MaximumHorizontalError} |             |             |
| {MinimumHorizontalError} |             |             |
| {AverageHorizontalError} |             |             |
| {RouteWaypoints}         |             |             |
| {ImportedFrom}           |             |             |
<br/>


## Command-Line Examples
*\*All of these examples assume the folder containing `42fdr.py` is in the PATH.*  
<br/>

### ✅ Minimal Usage
---
Converts a single file using default aircraft and config.
By default, output is saved in the current working folder.


##### **Windows (via `42fdr.bat` in PATH)**
```cmd
42fdr tracklog.csv
```

##### **Linux/macOS**
```bash
42fdr.py tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🧮 Convert Multiple Files
---
Processes multiple track logs in one command.  

##### **Windows**
```cmd
42fdr tracklog-1.csv tracklog-2.kml
```

##### **Linux/macOS**
```bash
42fdr.py tracklog-1.csv tracklog-2.kml
```
<br/>

**Creates:**
- `tracklog-1.fdr`
- `tracklog-2.fdr`

<br/>

### 📁 Override Output Folder
---
Save `.fdr` files to a specific folder instead of next to the input files.

##### **Windows**
```cmd
42fdr -o %USERPROFILE%\Desktop tracklog-1.csv tracklog-2.kml
```

##### **Linux/macOS**
```bash
42fdr.py -o ~/Desktop tracklog-1.csv tracklog-2.kml
```
<br/>

**Creates:**
- `Desktop/tracklog-1.fdr`
- `Desktop/tracklog-2.fdr`

<br/>

### 🛩️ Specify Aircraft
---
Override the aircraft specified in the config.  


##### **Windows**
```cmd
42fdr -a "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf" tracklog.csv
```

##### **Linux/macOS**
```bash
42fdr.py -a "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf" tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🧭 Apply Origin and Destination Offsets
---
Apply local replay offsets at the first and/or last track points using feet in `east,north,up` order.

##### **Windows**
```cmd
42fdr -O "2,0,-15.5" -D "0,0,-10" tracklog.csv
```

##### **Linux/macOS**
```bash
42fdr.py -O "2,0,-15.5" -D "0,0,-10" tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🛬 Apply an Origin Offset Only
---
Use just `-O` when departure and arrival are at the same airfield; `-D` is not needed in that case.

##### **Windows**
```cmd
42fdr -O "0,0,-12" tracklog.csv
```

##### **Linux/macOS**
```bash
42fdr.py -O "0,0,-12" tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🗺️ Enable Airfield Database
---
Enable local OurAirports lookup so waypoint coordinates can be resolved from database identifiers.

##### **Windows**
```cmd
42fdr --airfieldDB tracklog.csv
```

##### **Linux/macOS**
```bash
42fdr.py --airfieldDB tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🛠️ Use Custom Config File
---
Load settings (e.g. aircraft, timezone, DREFs, output path) from a custom config file.

##### **Windows**
```cmd
42fdr -c %USERPROFILE%\configs\custom.ini tracklog.kml
```

##### **Linux/macOS**
```bash
42fdr.py -c ~/configs/custom.ini tracklog.kml
```
<br/>

**Creates:**
- `<path/defined/in/config>/tracklog.fdr`
