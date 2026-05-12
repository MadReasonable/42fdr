# ✈️ How to Replay ForeFlight Logs in X-Plane 12 Using 42fdr (Windows)

## Install Python (if needed)

If you're not sure whether Python is installed, open Command Prompt and run:

```bash
python --version
```

If that doesn't work or the version is older than 3.9, download and install the latest version from:  
[https://www.python.org/downloads/windows/](https://www.python.org/downloads/windows/)

✅ Be sure to check **"Add Python to PATH"** during installation.

## Download and Set Up 42fdr

**1. Download**
- Go to: [https://github.com/MadReasonable/42fdr/releases](https://github.com/MadReasonable/42fdr/releases)
- Download the latest release in your preferred format (`zip`, `tar.gz`)

**2. Extract the contents to a working folder**  
Windows:

```cmd
%USERPROFILE%\42fdr
```
*(e.g. C:\\Users\\\<yourname>\\42fdr)*

macOS/Linux:
```bash
~/.local/42fdr
```

**3. (Optional) Add to your PATH**  
To make `42fdr.py` accessible from any folder:
- Press `Win + R`, type `sysdm.cpl`, hit Enter
- Go to the **Advanced** tab -> click **Environment Variables**
- Under "User variables", edit `Path` and add:

```text
C:\Users\<yourname>\42fdr
```

## Usage: Convert a ForeFlight Log

42fdr supports both CSV and KML files (but not GPX files).  
Export tracklog files using the official instruction below:  
📄 [ForeFlight Track Log Export Guide](https://support.foreflight.com/hc/en-us/articles/27632618331927-How-can-a-Track-Log-be-exported)

Running `42fdr.py` on a tracklog file creates an `.fdr` file with the same base name.  
By default, the output is saved to the current working directory, but you can change it using `--outputFolder` or a config file.

**Basic Usage:**

```bash
python 42fdr.py tracklog.csv
```

**With aircraft and timezone:**

```bash
python 42fdr.py tracklog.kml --aircraft "Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf" --timezone 5
```

**With config file:**

```bash
python 42fdr.py tracklog.csv --config 42fdr.ini
```

**Multiple tracks at once:**

```bash
python 42fdr.py tracklog-1.csv tracklog-2.csv tracklog-3.kml
```

```bash
python 42fdr.py tracklog-*.csv
```

## Importing into X-Plane

From the main menu:
1. Select **Load Saved Flight**
2. Select **Open Saved Flight Not Listed**
3. Navigate to the folder where you saved the `.fdr` file(s)
4. Select an `.fdr` file to replay

## Using a Config File

Config files are optional, but they simplify command-line usage.  
They support custom data columns, trim/calibration for AHRS data, and automatic aircraft selection by tail number.

By default, 42fdr looks for `42fdr.conf` or `42fdr.ini`, first in the current working directory, then in the same folder as `42fdr.py`.

You can also specify a custom config path using `--config`.

**Supported Config Sections:**

| Section | Purpose |
| --- | --- |
| `[Defaults]` | Defaults for command-line arguments (e.g., aircraft path, timezone) |
| `[Aircraft/*]` | Automatically selects aircraft based on tail number |
| `[Tail <Tail#>]` | Trim/correct AHRS data for a specific aircraft |
| `[AirfieldDB]` | Enables and configures OurAirports.com database lookup and filtering behavior |
| `[Waypoint <Name>]` | Offset/correct replay positions around airports (fix floating during taxi) |

## DREF Support

The core FDR format only supports timestamped AHRS data (heading, pitch, roll).  
DREFs allow you to define additional cockpit instruments like airspeed, altitude, or compass heading.

42fdr supports flexible DREF mapping globally, by aircraft, or by tail number.

## Waypoints and Offsets

Use waypoint offsets to fix replay ground contact at specific airports (for example, if the airplane floats during taxi).


**Example config:**

```ini
[Defaults]
Aircraft    = Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP.acf
TimezoneCSV = 5

DREF sim/cockpit2/gauges/indicators/airspeed_kts_pilot = round({Speed}, 4), 1.0, IAS
DREF sim/cockpit2/gauges/indicators/altitude_ft_pilot = round({ALTMSL}, 4), 1.0, Altimeter
DREF sim/cockpit2/gauges/indicators/compass_heading_deg_mag = round({HEADING}, 3), 1.0, Compass

[Aircraft/AeroSphere Piper Warrior/PiperWarrior/PiperWarrior.acf]
Tails = N123ND, N321ND

DREF sim/cockpit2/gauges/indicators/heading_vacuum_deg_mag_pilot = round({HEADING}, 3), 1.0, Vacuum Heading
DREF sim/cockpit2/gauges/indicators/pitch_vacuum_deg_pilot = round({PITCH}, 3), 1.0, Vacuum Pitch
DREF sim/cockpit2/gauges/indicators/roll_vacuum_deg_pilot = round({ROLL}, 3), 1.0, Vacuum Roll
DREF sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot = 29.92, 1.0, Barometer

[Tail N123ND]
headingTrim = 7.0
pitchTrim   = 0.0
rollTrim    = 0.0

[AirfieldDB]
enabled = true
MaxAgeDays = 90

[Waypoint KBED]
offset = 0.0, 0.0, -20.0

[Waypoint 8MA4]
hideFromRoute = true
```
