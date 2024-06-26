#!/bin/bash
set -e


for i in $(seq 0 19)
do 
    sleep 5s 
	python swe_procedure-loop.py 
done  
