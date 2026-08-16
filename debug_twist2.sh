#!/bin/bash
set -e
TOKEN=$(curl -sS -X POST http://localhost:8080/api/auth/login -H 'Content-Type: application/json' -d '{"passcode":"khiemgom"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
AUTH="Authorization: Bearer $TOKEN"

curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/debug_twist/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"geometry_msgs/msg/Twist","source":"send [[1,2,3],[4,5,6]] hz 1 dur 10s;"}'
