FROM ghcr.io/shridarpatil/frappe

LABEL org.opencontainers.image.source=https://github.com/shridarpatil/whatsapp_evolution
MAINTAINER Shridhar <shridharpatil2792@gmail.com>
RUN bench get-app https://github.com/shridarpatil/whatsapp_evolution.git --skip-assets
