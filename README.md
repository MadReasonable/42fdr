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

If that doesn't work, or if the version is older than 3.9, download and install the latest version from:

https://www.python.org/downloads/

✅ **Be sure to check “Add Python to PATH” during installation.**  
<br/>

### Step 2 – Download 42fdr
---

1. Get the lateest release from the GitHub:

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
42fdr [-c configFile] [-a aircraft] [-t timezone] [-o outputFolder] trackFile1 [trackFile2 ...]
```

macOS/Linux:
```bash
42fdr.py [-c configFile] [-a aircraft] [-t timezone] [-o outputFolder] trackFile1 [trackFile2 ...]
```

42FDR will convert one or more files, rename it with the `.fdr` extension, and save the output to the current working directory.

42fdr supports both CSV and KML files, but CSV are preferred as they provide more metadata in a smaller, simpler file.
Either format will produce equally valid FDR files.


| Options | Description |
|---------|-------------|
| `-c`    | Specify a config file.  A config file can be used to set the options below instead of on the command-line.  A config file can also define custom DREFs, automatically lookup an X-Plane aircraft by tail number, and load tail specific attitude calibrations.
| `-a`    | Choose an X-Plane aircraft.  X-Plane requires the FDR file to specify an aircraft model, which is not included in the ForeFlight track file.  `Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf` is used by default unless overridden by a config file or command line option.
| `-t`    | Adjust all times by this (positive or negative) amount.  If you've recorded your flight in local time, this value should be the *opposite* of your actual timezone. It will be added to recorded timestamps to get Zulu time.  Can be expressed as a decimal number of hours (e.g. `3.5`) or in the format +/-hh:mm[:ss] (e.g. `-5:00`)
| `-o`    | Choose a different output path.  
<br/>


## Using a config file
Config files are optional.
They can be used to avoid passing long parameters on the command line, to compute additional columns for replay, to automatically load the correct X-Plane model for the recorded tail number, and to correct for heading/pitch/roll deviations in specific aircraft.

42fdr will automatically search for a config file named either 42fdr.conf or 42fdr.ini, first in the folder from where you run 42fdr.py and then in the folder where 42fdr.py lives.
You can also specify a custom config file location from the command line.

An example configuration is provided with the name `42fdr.conf.example`.
Make a copy or rename it, then edit it as needed.
One `[Defaults]` section and as many `[<Aircraft/*>]` and `[<Tail>]` sections as needed are supported.


### 42fdr.conf example:
```ini
[Defaults]
Aircraft    = Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf
Timezone    = 5   # CSV files record times in Local Time
TimezoneKML = 0   # KML files record times in UTC
OutPath     = .

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
<br/>

### DREF Definitions
---
The required, default fields in an X-Plane FDR file only include time, position, attitude.
This is enough to make your simulated aircraft follow the track, but cockpit instruments won’t function correctly without additional data.
To get instruments like the airspeed indicator and artificial horizon working, additional fields must be added to the FDR file to provide the appropriate values.

`DREF` keys allow you to add additional fields to the output FDR file.
ForeFlight only provides basic position, attitude, and ground speed. This feature can be used to copy those values to additional fields `(e.g. ground speed to airspeed indicator)`, to pass constant values `(e.g. 29.92)`, and to compute new values `(e.g. round({Pitch}, 3))`.

You can define custom DREFs in any section:
- `[Defaults]` — applies to all flights
- `[Aircraft/...]` — applies to flights using that aircraft model
- `[Tail]` — applies to flights from that specific tail number

<br/> 

Each DREF key must begin with `DREF` followed by the dataref path. This path is the name of the X-Plane dataref to set, and must match exactly:
```ini
DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = {Speed}, 1.0, IAS
```
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

A single key is supported, `Tails`, which can be used to list all tail numbers which should cause this aircraft to be used in the output file `(e.g. N1234X, N5678Y)`

DREFs defined in this section will be included in FDR files generated for this aircraft model.  
<br/>

### [\<Tail>] Sections
---
\<Tail> sections allow for correction of attitude information in the flight track.
\<Tail> section names are just airplane registration numbers `(e.g. N1234X)`.

These sections support:
- `headingTrim`, `pitchTrim`, `rollTrim` — These offsets will be added to attitude data for every track point.

DREFs defined in this section will be included in FDR files generated for this specific tail number.

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


#### **Windows (via `42fdr.bat` in PATH)**
```cmd
42fdr tracklog.csv
```

#### **Linux/macOS**
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

#### **Windows**
```cmd
42fdr tracklog-1.csv tracklog-2.kml
```

#### **Linux/macOS**
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

#### **Windows**
```cmd
42fdr -o %USERPROFILE%\Desktop tracklog-1.csv tracklog-2.kml
```

#### **Linux/macOS**
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


#### **Windows**
```cmd
42fdr -a "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf" tracklog.csv
```

#### **Linux/macOS**
```bash
42fdr.py -a "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf" tracklog.csv
```
<br/>

**Creates:**
- `tracklog.fdr`

<br/>

### 🛠️ Use Custom Config File
---
Load settings (e.g. aircraft, timezone, DREFs, output path) from a custom config file.

#### **Windows**
```cmd
42fdr -c %USERPROFILE%\configs\custom.ini tracklog.kml
```

#### **Linux/macOS**
```bash
42fdr.py -c ~/configs/custom.ini tracklog.kml
```
<br/>

**Creates:**
- `<path/defined/in/config>/tracklog.fdr`
