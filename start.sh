#!/bin/bash
f_name=led_web_test.py
dir=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
logdir="log"
# 确保日志目录存在
mkdir -p "${logdir}"
pid=`ps -ef |grep $dir/$f_name | grep -v grep |awk '{print $2}'`
for id in $pid
do
    kill -9 $id
done
nohup /usr/bin/python3 -u $dir/$f_name > $logdir/info.log 2>&1 &
