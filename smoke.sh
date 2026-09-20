#!/usr/bin/env bash
# Smoke test for a running atlas:  ./smoke.sh [BASE_URL]   (default http://localhost:8000)
# Checks every page type returns the expected status. Admin checks run when
# ADMIN_PASSWORD is set in the environment. Exit code = number of failures.
BASE="${1:-http://localhost:8000}"
fail=0
check() {  # expected-status path [curl args...]
  local want="$1" path="$2"; shift 2
  local got; got=$(curl -s -o /tmp/smoke.body -w "%{http_code}" "$@" "$BASE$path")
  if [[ "$got" == "$want" ]]; then printf "ok   %s %s\n" "$got" "$path"
  else printf "FAIL %s (want %s) %s\n" "$got" "$want" "$path"; fail=$((fail+1)); fi
}
first() { grep -o "$1" /tmp/smoke.body | head -1 | sed -E "$2"; }

check 200 /healthz
check 200 /
check 200 "/books?q=the"
BOOK=$(first 'href="/book/[0-9]*"' 's/.*book\/([0-9]*)"/\1/')
check 200 "/book/$BOOK"
ORN=$(first 'href="/ornament/[^"]*"' 's/.*ornament\/([^"]*)"/\1/')
check 200 "/ornament/$ORN"
check 200 "/ornament/$ORN?sim=cluster"
check 200 "/img/crop/$ORN.jpg?w=300"
check 200 "/img/crop/$ORN.jpg?w=900"
check 307 "/img/page/$ORN"
check 200 "/api/book/$BOOK/strip"
check 200 "/api/ornament/$ORN"
check 200 "/api/search/books?q=essay"
check 200 "/books?q=fabel+of&sort=year&dir=asc"
check 200 /about
check 200 /reprints
check 200 /compare
check 200 "/api/class/geo?c=HP-ann:superclass:C014"
check 200 "/api/class/geo?c=HP-pred:cluster:21"
check 404 "/api/class/geo?c=nonsense"
check 200 "/class/HP-ann/superclass/C014?book=0100147100"
check 200 /static/vendor/d3-7.9.0.min.js
check 200 /static/geo/land-50m-simplified.json
check 200 "/compare?a=HP-ann:superclass:C014&a_place=london&b=HP-ann:superclass:C014&b_place=-london"
check 200 "/compare?a_q=C014&b_q=HP-21"
check 200 "/compare?a=HP-ann:superclass:C014&a_agent=tonson&b=HP-ann:superclass:C014&b_agent=-tonson"
check 200 "/compare?a_q=nothingmatchesthis"
check 200 "/api/classes/suggest?q=C01"
check 200 "/api/classes/suggest?q=HP-21"
check 200 "/api/class/facets?c=HP-ann:superclass:C014"
check 404 "/api/class/facets?c=bad"
check 200 "/class/HP-ann/superclass/C014?place=dublin"
check 200 "/class/HP-ann/superclass/C014?agent=-tonson&p=1"
check 200 "/class/DI-ann/superclass/A_Heart"
check 200 "/reprints?all=1"
check 200 /reprints.csv
check 200 /search/image
check 200 "/api/image-search/status"
check 404 /search/image/deadbeefdeadbeef
check 200 "/class/HP-ann/superclass/C014?place_min=3&place_d=0.5"
check 200 /classes
for tax in HP-ann:superclass HP-ann:subclass HP-ann:variant DI-ann:superclass HP-pred:cluster DI-pred:cluster TP-pred:cluster; do
  check 200 "/classes?tax=$tax&sort=span&dir=asc"
  CL=$(first 'href="/class/[^"]*"' 's/.*href="([^"]*)"/\1/')
  [[ -n "$CL" ]] && check 200 "$CL" && check 200 "$CL?order=group"
done
check 200 "/class/HP-ann/superclass/C005"
check 200 "/class/HP-ann/variant/C005/C005_01/a"
check 200 "/img/class/HP-ann/superclass/C005.jpg"
check 301 "/class/superclass/C005"
check 301 "/class/variant/C005/C005_01/a"
check 404 "/class/HP-ann/superclass/NOPE"
check 404 "/class/HP-ann/cluster/1"
check 200 /agents
check 200 "/agents?sort=owned&dir=desc"
check 200 /agent/tonson
check 200 "/agent/tonson?view=owned"
check 200 "/agent/tonson?view=owned&joint=1&srcset=1&src=HP-ann"
check 200 "/agent/tonson?srcset=1"
check 200 /agents/pair/tonson/lintot
check 200 /lending
check 200 "/lending?owner=0.6&lo=0.05&hi=0.3&min_books=5&gap=5&agent=tonson"
check 200 /lending.csv
check 404 /no/such/page
check 404 /book/0000000000
check 403 /admin/workbench/save -X POST -H 'Content-Type: application/json' -d '{}'

if [[ -n "$ADMIN_PASSWORD" ]]; then
  J=/tmp/smoke.jar; rm -f $J
  check 401 /admin/login -X POST -d "name=smoke&password=wrong"
  check 303 /admin/login -X POST -d "name=smoke&password=$ADMIN_PASSWORD" -c $J
  check 200 /admin -b $J
  check 200 /admin/reports -b $J
  check 200 "/admin/reports?status=all" -b $J
  check 200 "/admin/workbench/HP-ann/superclass/C005" -b $J
  check 200 "/admin/workbench/DI-pred/cluster/1" -b $J
  check 200 "/lending" -b $J
fi
echo "failures: $fail"
exit $fail
