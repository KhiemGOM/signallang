#!/bin/bash
source /opt/ros/humble/setup.bash
source ~/robohome_ws/install/setup.bash
export PYTHONPATH="/home/khiemgom/dashboard-env/lib/python3.10/site-packages:$PYTHONPATH"
python3 /home/khiemgom/patternlang/debug_twist.py
