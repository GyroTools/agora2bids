# Runs on top of gyrotools/gtagora-connector-py's "-converter" image
# (https://github.com/GyroTools/gtagora-connector-py/tree/master/Docker/converter), which already has everything
# agora2bids needs: gtagora-connector (from source), numpy, parrec2dcm (Python >= 3.10, hence that image's own
# python:3.10 base) and dcm2niix. Only agora2bids itself is added here.
FROM gyrotools/gtagora-connector-py:master-converter
COPY . /src
WORKDIR /src
# --no-deps: every dependency is already installed by the base image; skip pip's own dependency resolution so it
# can't try to re-fetch or upgrade any of them (gtagora-connector in particular isn't meant to be taken from PyPI
# here -- the base image builds it from its own source checkout).
RUN pip install --no-cache-dir --no-deps .
ENTRYPOINT ["agora2bids"]
