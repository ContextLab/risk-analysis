FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /mnt

RUN pip install --no-cache-dir \
        jupyter notebook \
        httpx networkx matplotlib pydantic platformdirs pytest

ENTRYPOINT ["/usr/bin/env"]
CMD ["bash"]