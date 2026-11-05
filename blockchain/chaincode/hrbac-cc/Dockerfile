FROM golang:1.22.5-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /hrbac-cc .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /hrbac-cc /hrbac-cc
USER nonroot
EXPOSE 9999
ENTRYPOINT ["/hrbac-cc"]
