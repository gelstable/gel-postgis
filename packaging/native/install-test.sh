#!/usr/bin/env bash
# Run inside Debian 12 or Rocky Linux 9, with packages at /packages.
# Usage: install-test.sh deb|rpm amd64|arm64|x86_64|aarch64 SLOT
set -euo pipefail
format="${1:?}"; arch="${2:?}"; slot="${3:?}"
shopt -s nullglob
packages=(/packages/gel-server-"$slot"-ext-postgis-*"-$arch.$format")
test "${#packages[@]}" = 1
server_name="gel-server-$slot"
extension_name="$server_name-ext-postgis"
version() {
  if [[ "$format" == deb ]]; then dpkg-query -W -f='${Version}' "$server_name";
  else rpm -q --qf '%{EPOCH}:%{VERSION}-%{RELEASE}' "$server_name"; fi
}
# These packages are built from the same verified server tarball used by gel-load-ext.
servers=(/server-packages/gel-server-"$slot"-*"-$arch.$format")
test "${#servers[@]}" = 1
if [[ "$format" == deb ]]; then
  dpkg -i "${servers[0]}"
else
  rpm -i "${servers[0]}"
fi
before=$(version)
if [[ "$format" == deb ]]; then dpkg -i "${packages[0]}";
else rpm -i "${packages[0]}"; fi
test "$(version)" = "$before"
getent passwd gel
getent group gel
test "$(stat -c %a /var/lib/gel)" = 755
test "$(stat -c %U:%G /var/lib/gel)" = gel:gel
for command in gel-server edgedb-server; do
  test "$(readlink "/usr/bin/$command-$slot")" = "../lib/gel-server-$slot/bin/gel-server"
done
test -f "/lib/systemd/system/gel-server-$slot.service"
test ! -e "/etc/systemd/system/multi-user.target.wants/gel-server-$slot.service"
# Small explicit pool limits keep the test affordable on emulated local runners.
mkdir -p /run/gel
chown gel:gel /run/gel
server="/usr/bin/gel-server-$slot"
args=(--data-dir="/var/lib/gel/$slot/data" --runstate-dir=/run/gel
      --tls-cert-mode=generate_self_signed --max-backend-connections=10
      --compiler-pool-size=1)
runuser -u gel -- "$server" "${args[@]}" --bootstrap-only
setpriv --reuid=gel --regid=gel --init-groups "$server" "${args[@]}" > /tmp/gel-server.log 2>&1 &
pid=$!
cleanup() { kill -INT "$pid" 2>/dev/null || true; wait "$pid" || true; }
trap cleanup EXIT
ready=false
for ((attempt=0; attempt<120; attempt++)); do
  if /cli/gel --admin --unix-path /run/gel query 'SELECT 1 + 1' > /tmp/query.out 2>/tmp/query.err; then
    ready=true
    break
  fi
  if ! kill -0 "$pid" 2>/dev/null; then cat /tmp/gel-server.log; exit 1; fi
  sleep 1
done
if [[ "$ready" != true ]]; then cat /tmp/gel-server.log /tmp/query.err; exit 1; fi
cat /tmp/query.out
grep -qx 2 /tmp/query.out
/cli/gel --admin --unix-path /run/gel query 'START MIGRATION TO { using extension postgis; }; POPULATE MIGRATION; COMMIT MIGRATION;'
/cli/gel --admin --unix-path /run/gel query 'SELECT ext::postgis::x(ext::postgis::makepoint(1.0, 2.0))' > /tmp/postgis.out
cat /tmp/postgis.out
grep -qx 1 /tmp/postgis.out
test "$(version)" = "$before"
cleanup
trap - EXIT
test -d "/var/lib/gel/$slot/data"
touch "/var/lib/gel/$slot/data/native-package-preserve"
if [[ "$format" == deb ]]; then
  dpkg --purge "$extension_name"
  test "$(version)" = "$before"
  dpkg --purge "$server_name"
else
  rpm -e "$extension_name"
  test "$(version)" = "$before"
  rpm -e "$server_name"
fi
getent passwd gel
test -f "/var/lib/gel/$slot/data/native-package-preserve"
test ! -e "$server"
test ! -e "/usr/bin/edgedb-server-$slot"
test ! -e "/lib/systemd/system/gel-server-$slot.service"
echo "Installed PostGIS, applied schema, queried geometry, stopped and removed $format/$arch; account/data preserved"
