#!/bin/sh
# Picks the demo's input. Generated data (make data) gives a full ward; on a
# clean clone, or with WARDWATCH_DEMO_INPUT=fixtures, the committed fixtures
# stand in: three stays, one of which deteriorates within its first hours.
# Extra arguments go to `wardwatch-sim replay`.
set -eu
physionet=/fixtures/physionet
synthea=/fixtures/synthea
sites=A
# Three fixture identities can fill three beds at a time.
beds=3
if [ "${WARDWATCH_DEMO_INPUT:-auto}" != fixtures ]; then
  if [ -d /data/physionet/training_setA ]; then
    physionet=/data/physionet
    sites=A,B
  fi
  if ls /data/synthea/fhir/*.json >/dev/null 2>&1; then
    synthea=/data/synthea/fhir
    beds=12
  fi
fi
echo "demo: replaying $physionet (sites $sites) with identities from $synthea across $beds beds"
exec wardwatch-sim replay --host "$WARDWATCH_SIM_HOST" --port "$WARDWATCH_SIM_PORT" \
  --physionet-dir "$physionet" --synthea-dir "$synthea" --sites "$sites" --beds "$beds" "$@"
