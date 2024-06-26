

#!/bin/bash
set -e

for j in 15 16 17 18 19
do 
	for i in $(seq 0 9)
	do 
		python swe_procedure-test1.py --pb_freq 4.0 --pb_focus $j --pb_ap 29 --rep $i
	done  
done 

