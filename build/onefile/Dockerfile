# Refresh this multi-platform digest from base-debian12:latest when updating the base image.
FROM gcr.io/distroless/base-debian12@sha256:fabbf1c0c357a3d42550111351daed089b20a2c954df13ee2fcff60602515e84

COPY output/app.bin /app
COPY migrations /migrations

CMD ["/app"]
