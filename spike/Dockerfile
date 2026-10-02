FROM python:3.10-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "numpy<1.24" "opencv-python<4.10" oemer

WORKDIR /work
