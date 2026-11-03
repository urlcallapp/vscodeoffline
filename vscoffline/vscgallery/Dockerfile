FROM python:3.12-alpine
WORKDIR /opt/vscoffline

# For gunicorn > 20.0.0
RUN apk --no-cache add libc-dev binutils

COPY ./vscoffline/ ./

RUN mkdir /artifacts/

RUN pip install --no-cache-dir -r vscgallery/requirements.txt

ENV ARTIFACTS=/artifacts
ENV BIND=0.0.0.0:443
ENV TIMEOUT=180
ENV THREADS=4
ENV SSLARGS="--certfile=/run/tls/tls.crt --keyfile=/run/tls/tls.key"

CMD gunicorn  \
    $SSLARGS \
    --bind $BIND \
    --access-logfile - \
    --timeout $TIMEOUT \
    --threads $THREADS \
    "server:create_app()"
