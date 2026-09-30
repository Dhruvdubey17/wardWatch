# Build context is the repository root, because the build reads contracts/.
#   docker build -f ingest/Dockerfile -t wardwatch-ingest .

FROM ubuntu:24.04 AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates clang cmake ninja-build pkg-config librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY contracts/ contracts/
COPY ingest/ ingest/
RUN rm -rf ingest/build \
    && cmake -S ingest -B /build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
        -DWARDWATCH_BUILD_TESTS=OFF -DWARDWATCH_BUILD_BENCH=OFF -DWARDWATCH_FETCH_RDKAFKA=OFF \
    && cmake --build /build --target wardwatch-ingest

FROM ubuntu:24.04
RUN apt-get update \
    && apt-get install -y --no-install-recommends librdkafka1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 wardwatch
COPY --from=build /build/apps/wardwatch-ingest /usr/local/bin/wardwatch-ingest
USER wardwatch
EXPOSE 2575 9464
# Inside the container the listener binds every interface so other services
# on the Compose network can reach it; the host publishes it on 127.0.0.1 only.
ENTRYPOINT ["/usr/local/bin/wardwatch-ingest", "--bind", "0.0.0.0", "--metrics-bind", "0.0.0.0"]
CMD ["--sink", "kafka", "--kafka-brokers", "kafka:9092"]
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/9464 && printf 'GET /healthz HTTP/1.0\\r\\n\\r\\n' >&3 && grep -q ok <&3"]
