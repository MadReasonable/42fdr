# 42fdr
Python script to convert ForeFlight's exported flight tracks to X-Plane compatible FDR files

**Only works with CSV files currently*


## Installation
Just put 42fdr.py somewhere on your computer.
- Works with python 3.9 and above.
- Single file with no 3rd-party dependencies.


## Usage
`[python3] 42fdr.py [-a Aircraft] [-o outputFolder] trackFile1 [trackFile2, trackFile3, ...]`

You should be able to run 42fdr without explicitly invoking the python interpreter.
It will convert one or more files, rename it with the `.fdr` extension, and save the output to the current working directory.
Choose a different output path with the `-o` parameter.

X-Plane requires the FDR file to specify an aircraft model for the flight and this is not provided by the ForeFlight track file.
`Aircraft/Laminar Research/Cessna 172 SP/Cessna_172SP_G1000.acf` will be used by default. 
Choose a different aircraft with the `-a` parameter.


## Examples

<b style='font-size:smaller'>`./42fdr.py tracklog-E529A53E-FBC7-4CAC-AB46-28C123A9038A.csv`</b>

The simplest use case.  Python is installed and configured correctly, we are in a bash shell, the script is in the same folder as the track file, we are only converting one file, using the default aircraft, and it should be saved to the current folder.

This will create the file:
- `./tracklog-E529A53E-FBC7-4CAC-AB46-28C123A9038A.fdr`

---
<b style='font-size:smaller'>`python3 42fdr.py -a "Aircraft/Laminar Research/Lancair Evolution/N844X.acf" tracklog-E529A53E.csv`</b>

The same as above, except the Python interpreter is called explicitly, which is needed when using Windows, and the aircraft is changed to the Lancair Evolution.

This will create the file:
- `.\tracklog-E529A53E.fdr`

---
<b style='font-size:smaller'>`python3 C:\Users\MadReasonable\bin\42fdr.py -o /Users/MadReaonble/Desktop/ tracklog-E529A53E.csv tracklog-DC7A92F3.csv`</b>

Convert more than one file and send the output to the desktop.
The script is not in the current working directory.

This will create the files:
- `C:\Users\MadReasonable\bin\tracklog-E529A53E.fdr`
- `C:\Users\MadReasonable\bin\tracklog-DC7A92F3.fdr`