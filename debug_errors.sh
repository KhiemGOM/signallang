#!/bin/bash
TOKEN=$(curl -sS -X POST http://localhost:8080/api/auth/login -H 'Content-Type: application/json' -d '{"passcode":"khiemgom"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
AUTH="Authorization: Bearer $TOKEN"

curl -sS -X POST http://localhost:8080/api/graph/topics/debug_twist/fake_publish/stop -H "$AUTH" -H "Content-Type: application/json" -d '{"session_id":"32bec5680a104a8fa928eb40d7bfc6d5"}'
echo

echo "=== 1. syntax error (missing semicolon-ish garbage) ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t1/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"std_msgs/msg/Bool","source":"data = ;"}'

echo "=== 2. unmatched brace ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t2/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"std_msgs/msg/Bool","source":"if t < 1 {\n data = true;\n"}'

echo "=== 3. dur inf mid-body compile error ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t3/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"std_msgs/msg/Bool","source":"repeat {\n data = true;\n send dur inf;\n data = false;\n send dur 1s;\n}"}'

echo "=== 4. positional fill length mismatch ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t4/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"geometry_msgs/msg/Twist","source":"send [1,2,3];"}'

echo "=== 5. unknown field name in path ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t5/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"geometry_msgs/msg/Twist","source":"nonexistent_field = 5;\nsend;"}'

echo "=== 6. unresolvable message type ==="
curl -sS -w '\nHTTP %{http_code}\n' -X POST http://localhost:8080/api/graph/topics/t6/fake_publish/start \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"type":"totally_fake_pkg/msg/Nope","source":"send;"}'
