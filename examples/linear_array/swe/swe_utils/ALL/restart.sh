#!/bin/bash

set -e 

#killall run_test.sh
killall python
cd /home/damian/src/us4r-api/drivers/linux
sudo ./install
cd -
