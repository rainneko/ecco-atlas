#!/bin/bash
# start server, exercise every route, stop server — all in one shell session
cd /home/claude/ecco-atlas
export DATABASE_URL="postgresql://postgres@/ecco?host=/tmp&port=5433"
export SECRET_KEY=dev ADMIN_PASSWORD=test123 CACHE_DIR=/tmp/ecco-cache
uvicorn app.main:app --host 127.0.0.1 --port 8099 >/tmp/uv.log 2>&1 &
PID=$!
for i in $(seq 1 25); do curl -sf http://127.0.0.1:8099/healthz >/dev/null && break; sleep 1; done

j() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; }
B=$(curl -s "http://127.0.0.1:8099/api/search/books?q=fable+of+the+bees&limit=1" | j "print(d['results'][0]['book_id'])")
O=$(curl -s "http://127.0.0.1:8099/api/book/$B/strip" | j "print([p['oid'] for r in d['rows'] for p in r['points']][0])")
META=$(curl -s "http://127.0.0.1:8099/api/ornament/$O" | j "print(d['subclass'],d['superclass'],d['class_path'],sep='|')")
SUB=${META%%|*}; REST=${META#*|}; SUP=${REST%%|*}; CP=${REST#*|}
echo "book=$B  orn=$O  sub=$SUB  sup=$SUP  path=$CP"
echo "--------------------------------------------------------------"
for u in "/" "/books" "/books?q=fable+of" "/books?tonson=1&kind=HP" "/book/$B" \
         "/ornament/$O" "/api/ornament/$O" "/api/book/$B/strip" \
         "/classes" "/classes?level=variant&q=C06" \
         "/class/subclass/$SUB" "/class/superclass/$SUP" "/class/variant/$CP" \
         "/agents" "/agents?q=tons" "/agent/tonson" "/admin/login" "/admin" \
         "/img/class/subclass/$SUB.jpg" "/ornament/nope" "/healthz"; do
  printf "%-46s %s\n" "$u" "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8099$u")"
done

echo "--------------------------------------------------------------"
echo "report + admin workflow:"
curl -s -o /dev/null -w "  submit report      %{http_code}\n" -X POST \
  -d "reporter=tester&sug_variant=c&note=chipped corner matches variant c" \
  "http://127.0.0.1:8099/ornament/$O/report"
curl -s -c /tmp/ecco-ck.txt -o /dev/null -w "  admin login        %{http_code}\n" -X POST \
  -d "password=test123" "http://127.0.0.1:8099/admin/login"
curl -s -b /tmp/ecco-ck.txt -o /tmp/admin.html -w "  admin queue        %{http_code}\n" "http://127.0.0.1:8099/admin"
RID=$(grep -o '/admin/report/[0-9]*' /tmp/admin.html | head -1 | grep -o '[0-9]*$')
echo "  open report id     ${RID:-none}"
curl -s -b /tmp/ecco-ck.txt -o /dev/null -w "  accept correction  %{http_code}\n" -X POST \
  -d "action=accept" "http://127.0.0.1:8099/admin/report/$RID"
curl -s -o /dev/null -w "  admin w/o cookie   %{http_code} (expect 303->login)\n" "http://127.0.0.1:8099/admin"
psql_out=$(PGPASSWORD= /usr/lib/postgresql/16/bin/psql "postgresql://postgres@/ecco?host=/tmp&port=5433" -t -c \
  "select count(*) from label_change" 2>/dev/null | tr -d ' ')
echo "  label_change rows  $psql_out"

echo "--------------------------------------------------------------"
echo "page render checks:"
curl -s "http://127.0.0.1:8099/book/$B"  | grep -c 'class="pt"'       | xargs echo "  strip dots on book page:"
curl -s "http://127.0.0.1:8099/books?q=fable" | grep -c 'class="pt"'  | xargs echo "  strip dots on books list:"
curl -s "http://127.0.0.1:8099/class/subclass/$SUB" | grep -c 'mixbar'| xargs echo "  publisher mix bars:"
curl -s "http://127.0.0.1:8099/agent/tonson" | grep -c 'class="thumb"'| xargs echo "  plate thumbnails on agent page:"
curl -s "http://127.0.0.1:8099/book/$B" | grep -o 'Books with the most similar[^<]*' | head -1
grep -iE "error|traceback|exception" /tmp/uv.log | head -5
kill $PID 2>/dev/null
wait $PID 2>/dev/null
echo "done"
