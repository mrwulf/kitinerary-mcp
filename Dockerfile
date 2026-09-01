FROM debian:trixie-slim

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    KITINERARY_EXTRACTOR_BIN=/usr/lib/x86_64-linux-gnu/libexec/kf6/kitinerary-extractor

# renovate: depName=KDE/kitinerary datasource=github-releases
ARG KITINERARY_VERSION=24.12.3

# kitinerary-extractor pulls in a real KDE Frameworks 6 + Qt 6 dependency
# chain (libqt6gui6/libqt6qml6 included, even headless) - there is no
# slimmer upstream package. Debian revision suffix (-1) is pinned exactly;
# see README for why Renovate can't fully automate this pin.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libkitinerary-bin=${KITINERARY_VERSION}-1 \
      libkitinerary-data=${KITINERARY_VERSION}-1 \
      python3 \
      python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --no-create-home --uid 10001 mcp

WORKDIR /app
COPY server.py requirements.txt ./
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

USER 10001
ENTRYPOINT ["python3", "server.py"]
