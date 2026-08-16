#!/bin/bash
TOKEN=$(curl -sS -X POST http://localhost:8080/api/auth/login -H 'Content-Type: application/json' -d '{"passcode":"khiemgom"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
AUTH="Authorization: Bearer $TOKEN"

curl -sS -X POST http://localhost:8080/api/graph/topics/t5/fake_publish/stop -H "$AUTH" -H "Content-Type: application/json" -d '{"session_id":"fac2f01651d4445f858d669f02007d3a"}'
echo

echo "=== hz 0 (ZeroDivisionError hypothesis) ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t7/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"std_msgs/msg/Bool","source":"data = true;\nsend hz 0;"}'
