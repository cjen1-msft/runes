ARG BASE_IMAGE="mcr.microsoft.com/azurelinux/base/core:3.0"
FROM ${BASE_IMAGE}

COPY ./tools /tools
COPY ./scripts /scripts

RUN tdnf install -y tmux vi 