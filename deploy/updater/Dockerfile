# The self-hosted updater (see updater.py): the Docker CLI with its compose
# plugin, plus Python for the small HTTP server. Built and released with the
# other images as handbook-updater.
FROM docker:27.3.1-cli

RUN apk add --no-cache python3
COPY updater.py /updater.py
EXPOSE 8080
CMD ["python3", "/updater.py"]
