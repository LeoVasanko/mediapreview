# Patched OnlyOffice Document Server with configurable converter worker count.
#
# The Community Edition hardcodes the document converter to 1 worker,
# which creates a severe bottleneck under concurrent load.
# This image patches the open-source license.js to spawn a configurable
# number of converter workers (default 8).
#
# Build:
#   docker build -t onlyoffice-mediapreview mediapreview/mediapreview/docker
#
# Run:
#   docker run -d -p 8988:80 \
#     -e WORKERS=16 \
#     -e JWT_SECRET=your-strong-secret \
#     --name onlyoffice-mediapreview onlyoffice-mediapreview
#
# JWT:
#   Set JWT_SECRET to the same value you pass to Cista as ONLYOFFICE_JWT_SECRET.
#   OnlyOffice will enable token validation automatically.
#
# The ONLYOFFICE_VERSION build arg lets you target a specific release.

ARG ONLYOFFICE_VERSION=9.3.1

FROM onlyoffice/documentserver:${ONLYOFFICE_VERSION}

# Prevent interactive apt prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install Node.js, npm, and git so we can run the FileConverter from source.
RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        nodejs \
        npm \
        git \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Pin the open-source server components to a known-good commit (~15 MB).
# The master branch is a moving target (the Linux/web tags are not published
# in the server repo): a 2026 convertermaster change there detects community
# edition as a "memory runtime" and forks NO converter workers, silently
# breaking all conversions. Pinned to the commit proven in production.
ARG ONLYOFFICE_SERVER_REF=4e56b8d056640557ffcd8c860a65535ab6cbd95b
RUN git init /opt/oo-server && \
    cd /opt/oo-server && \
    git remote add origin https://github.com/ONLYOFFICE/server.git && \
    git fetch --depth 1 origin "$ONLYOFFICE_SERVER_REF" && \
    git checkout FETCH_HEAD

# Patch license.js so the converter worker count is read from an env var
# instead of being hardcoded to 1.
RUN sed -i \
    's/count: 1,/count: parseInt(process.env.WORKERS, 10) || 8,/' \
    /opt/oo-server/Common/sources/license.js

# Defense in depth: never take the "memory runtime" branch that forks no
# converter workers, even if the pinned ref is bumped carelessly.
RUN sed -i \
    's/runtimeProfile\.isMemoryRuntime()/false \/* patched: always fork converter workers *\//g' \
    /opt/oo-server/FileConverter/sources/convertermaster.js

# Install npm dependencies for the modules the FileConverter touches.
# DocService deps are also needed because converter.js pulls in baseConnector.
RUN cd /opt/oo-server/Common      && npm ci --no-audit --no-fund
RUN cd /opt/oo-server/FileConverter && npm ci --no-audit --no-fund
RUN cd /opt/oo-server/DocService  && npm ci --no-audit --no-fund

# Back up the compiled pkg binary and replace it with our wrapper.
RUN mv /var/www/onlyoffice/documentserver/server/FileConverter/converter \
       /var/www/onlyoffice/documentserver/server/FileConverter/converter.orig

COPY converter-wrapper.sh /var/www/onlyoffice/documentserver/server/FileConverter/converter
RUN chmod +x /var/www/onlyoffice/documentserver/server/FileConverter/converter

# Default worker count (override at runtime with -e WORKERS=16).
ENV WORKERS=8

# Use our custom entrypoint to persist the env var to a file that the
# non-root converter process (user=ds) can read.
COPY entrypoint.sh /app/ds/run-document-server-patched.sh
RUN chmod +x /app/ds/run-document-server-patched.sh
ENTRYPOINT ["/app/ds/run-document-server-patched.sh"]
