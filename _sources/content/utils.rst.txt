.. _arrus-toolkit-utils:

=====
Utils
=====

us4OEM
======

Firmware update
---------------

::

    ./us4OEMFirmwareUpdate
    Allowed options:
    --help                produce help message
    --rpd-file arg        RPD bitstream file
    --sea-file arg        SEA bitstream file
    --sed-file arg        SED bitstream file
    --us4OEM-count arg    Number of us4OEM modules

Examples:

1. Display help message: ``./us4OEMFirmwareUpdate --help``
2. Update us4OEM ARRIA firmware only: ``/us4OEMFirmwareUpdate --us4OEM-count 2 --rpd-file .\us4oem_0xbd612458.rpd``
3. Update us4OEM TX firmware only: ``/us4OEMFirmwareUpdate --us4OEM-count 2 --sea-file us4oem_tx_a0a0a0a0a0.sea --sed-file us4oem_tx_a0a0a0a0a0.sed``
4. Update us4OEM ARRIA and TX firmware: ``./us4OEMFirmwareUpdate --us4OEM-count 2 --rpd-file .\us4oem_0xbd612458.rpd --sea-file us4oem_tx_a0a0a0a0a0.sea --sed-file us4oem_tx_a0a0a0a0a0.sed``

After updating us4R device system should be power-cycled.

Device Control
--------------

::

    ./Us4OEMControl
    Allowed options:
    --help                produce help message
    --system arg          us4rlite or us4r
    --oem-id arg          OEM ID (0/1 for us4rlite, 0-7 for us4r)
    --afe-write arg       Write AFE(x) register
    --afe-read arg        Read AFE(x) register
    --reg arg             AFE register address
    --value arg           AFE register value
    --io-std arg          Sets IO level (standard mode)
    --io-wave             Sets example IO waveform mode
    --seq-check           Prints sequencer ctrl & conf registers

Device Status
-------------

::

    ./Us4OEMStatus
    Allows to check FPGA firmware version, TX firmware version, FPGA temperature:
    --help                produce help message
    --clearrail           Clear UCD rail status
    --clearlog            Clear UCD rail fault logs
    --clearbb             Clear UCD rail fault black box
    --vucd                Prints UCD rail status and logs
    --nUS4OEM arg         Number of US4OEM devices
    --init arg            Execute us4oem initialization
    --calibrate_hvps      Performs HVPS voltage setting calibration and stores calibration vector in OEM+ flash memory (Since ARRUS 0.13.0)

High-Voltage supplier (HV256 or Us4RPSC)
========================================

::

    ./HVControl
    Allowed options:
    --help                produce help message
    --voltage arg         Set HV Voltage [V]
    --system arg          us4rlite or us4r
    --enable              enable HV
    --disable             disable HV
    --get                 Get set HV voltage
    --measure             Get measured HV voltages

DBAR-Lite
=========

::

    ./DBARLiteStatus
    Allowed options:
    --help                produce help message
    --id                  Read ID
    --update-firmware arg Update DBARLite FPGA firmware


Firmware update
---------------
Example:

::

    ./DBARLiteStatus --update-firmware path-to-firmware.rpd


Device Status
-------------
For example, to read device id:

::

    ./DBARLiteStatus --id

DBAR
====

::

    ./DBARStatus
    Allowed options:
    --help                produce help message
    --id                  Read ID
    --update-firmware arg Update DBAR FPGA firmware
    --dump-flash arg      Dump flash sector to a file
    --sector arg          Sector number
    --nUS4OEM arg         Number of US4OEM devices


Firmware update
---------------

Example:

::

    ./DBARStatus --nUS4OEM 8 --update-firmware path-to-firmware.rpd

Device Status
-------------

For example, to read device id:

::

    ./DBARStatus --id



