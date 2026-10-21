#!/usr/bin/env bash
# Linkerd's identity certificates, made with openssl (no `step` needed).
#
#   trust anchor  root.linkerd.cluster.local      CA, 10 years
#   issuer        identity.linkerd.cluster.local  intermediate CA, pathlen:0, 1 year
#
# Both are ECDSA P-256: Linkerd's identity controller only accepts that curve.
# Output goes to a git-ignored directory (default .demo/linkerd-certs/) and is
# idempotent: files that already exist are reused, so re-running setup-kind.sh
# does not rotate the mesh's identity under a running cluster.
#
# Usage: linkerd-certs.sh [OUT_DIR]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/.demo/linkerd-certs}"
mkdir -p "$OUT"
chmod 700 "$OUT"

if [[ -s "$OUT/ca.crt" && -s "$OUT/issuer.crt" && -s "$OUT/issuer.key" ]]; then
  echo "Linkerd certificates already present in $OUT, reusing them."
  exit 0
fi

# Trust anchor.
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/ca.key"
openssl req -x509 -new -key "$OUT/ca.key" -sha256 -days 3650 \
  -subj "/CN=root.linkerd.cluster.local" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -out "$OUT/ca.crt"

# Issuer: an intermediate CA that can sign workload certificates but no further CA.
openssl ecparam -name prime256v1 -genkey -noout -out "$OUT/issuer.key"
openssl req -new -key "$OUT/issuer.key" \
  -subj "/CN=identity.linkerd.cluster.local" -out "$OUT/issuer.csr"
cat > "$OUT/issuer.ext" <<'EXT'
basicConstraints=critical,CA:TRUE,pathlen:0
keyUsage=critical,keyCertSign,cRLSign
EXT
openssl x509 -req -in "$OUT/issuer.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
  -CAcreateserial -sha256 -days 365 -extfile "$OUT/issuer.ext" -out "$OUT/issuer.crt"
rm -f "$OUT/issuer.csr" "$OUT/issuer.ext" "$OUT/ca.srl"
chmod 600 "$OUT"/*.key
echo "Linkerd certificates written to $OUT."
