FROM golang:1.26.5-alpine3.24 AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go ./
COPY core ./core
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/browserstream ./main.go

FROM alpine:3.24.1
RUN apk add --no-cache ca-certificates wget && addgroup -S browserstream && adduser -S -G browserstream -h /app browserstream
WORKDIR /app
COPY --from=builder --chown=browserstream:browserstream /out/browserstream ./browserstream
COPY --chown=browserstream:browserstream files ./files
COPY --chown=browserstream:browserstream LICENSE NOTICE ./
USER browserstream
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["./browserstream"]
CMD ["-config", "/app/config.json"]
