#!/bin/bash
# Wrapper that runs the OnlyOffice FileConverter from patched Node.js source.
# Replaces the compiled pkg binary shipped with the Community Edition.

# The env var is not passed through supervisor to the 'ds' user, so we read
# it from a file written by the custom entrypoint.
if [ -z "${WORKERS}" ] && [ -r /tmp/oo-converter-workers.txt ]; then
  export WORKERS=$(cat /tmp/oo-converter-workers.txt)
fi

cd /opt/oo-server/FileConverter || exit 1

export NODE_ENV=production-linux
export NODE_CONFIG_DIR=/etc/onlyoffice/documentserver
export NODE_DISABLE_COLORS=1
export APPLICATION_NAME=onlyoffice
export LD_LIBRARY_PATH=/var/www/onlyoffice/documentserver/server/FileConverter/bin

exec node sources/convertermaster.js "$@"
