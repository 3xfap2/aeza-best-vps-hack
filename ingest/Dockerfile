# Сервис приёма событий: сборка и тесты в golang, в образе — один статический бинарник без shell, не от root.
FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
RUN go vet ./... && go test ./... && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /ingest .

FROM scratch
COPY --from=build /ingest /ingest
USER 65532:65532
EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 CMD ["/ingest", "-healthcheck"]
ENTRYPOINT ["/ingest"]
