#!/bin/bash
# Custom entrypoint that persists WORKERS to a file readable by
# the non-root user that supervisor uses to run the converter.

echo "${WORKERS:-8}" > /tmp/oo-converter-workers.txt
chmod 644 /tmp/oo-converter-workers.txt

exec /app/ds/run-document-server.sh "$@"
