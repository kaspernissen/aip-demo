# Backstage is built the same way every other component in this repo is
# built -- `docker build -t <image> <context>` with no host-side prerequisite
# (scripts/rebuild-and-deploy.sh's COMPONENTS convention; see its own header
# comment for why an out-of-band build step is avoided). Backstage's own
# scaffolded packages/backend/Dockerfile requires `yarn install`, `yarn tsc`
# and `yarn build:backend` to have already run on the HOST before `docker
# build` -- a second build pattern this repo does not use. This is instead
# the documented multi-stage alternative
# (https://backstage.io/docs/deployment/docker#multi-stage-build), which builds
# entirely inside the container from the app's own root -- the same
# self-contained shape every other component's Dockerfile already has.
#
# Reproduced from the upstream docs page verbatim except for two things: the
# base image tag is read from backstage.json's own "version" via the
# create-app scaffold rather than hand-copied twice, and this comment block
# replaces the doc's inline ones. Node 24 (`node:24-trixie-slim`) is
# upstream's own current docs pin for this Dockerfile, independent of the
# Node 22 this repo's other seven images and its host use -- Backstage does
# not publish a Node 22 base image variant for this Dockerfile, and mixing a
# host build step (which would need 22) with this container-only build
# (which needs 24) is exactly the risk multi-stage avoids: nothing outside
# this file cares what Node version built the image.

# Stage 1 - Create yarn install skeleton layer
FROM node:24-trixie-slim AS packages
WORKDIR /app
COPY backstage.json package.json yarn.lock ./
COPY .yarn ./.yarn
COPY .yarnrc.yml ./
COPY packages packages
# Comment this out if you don't have any internal plugins
COPY plugins plugins
RUN find packages \! -name "package.json" -mindepth 2 -maxdepth 2 -exec rm -rf {} \+

# Stage 2 - Install dependencies and build packages
FROM node:24-trixie-slim AS build
# Install sqlite3 dependencies, PLUS a C/C++ toolchain (python3, make, g++).
# The docs' own multi-stage Dockerfile lists only libsqlite3-dev, but
# `yarn install --immutable` fails on this base image without the toolchain --
# several of @backstage/cli's transitive native dependencies (cpu-features,
# tree-sitter, tree-sitter-json, ssh2's prerequisites) have no prebuilt binary
# for this platform, and node-gyp falls back to compiling from source, which
# needs a compiler this slim image does not ship. Every other component's
# Dockerfile builds a Python service with no C extensions to compile, so
# nothing else in this repo needs this.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends libsqlite3-dev python3 make g++ && \
    rm -rf /var/lib/apt/lists/*
USER node
WORKDIR /app
COPY --from=packages --chown=node:node /app .
RUN --mount=type=cache,target=/home/node/.cache/yarn,sharing=locked,uid=1000,gid=1000 \
    yarn install --immutable
COPY --chown=node:node . .
RUN yarn tsc
RUN yarn --cwd packages/backend build
RUN mkdir packages/backend/dist/skeleton packages/backend/dist/bundle \
    && tar xzf packages/backend/dist/skeleton.tar.gz -C packages/backend/dist/skeleton \
    && tar xzf packages/backend/dist/bundle.tar.gz -C packages/backend/dist/bundle

# Stage 3 - Build the actual backend image and install production dependencies
FROM node:24-trixie-slim
# libsqlite3-dev for better-sqlite3, plus MkDocs for TechDocs.
#
# TechDocs `builder: 'local'` generates a component's documentation site on
# first request, inside this container, which means the generator has to be
# here. The default `generator.runIn: 'docker'` shells out to a Docker daemon
# to run the techdocs container -- there is no daemon inside this pod and
# mounting one would be a far larger grant than this demo should hold, so
# app-config.production.yaml sets `runIn: 'local'` and this layer provides
# what "local" then needs: python3 and `mkdocs-techdocs-core`, the plugin
# bundle Backstage's own generator invokes.
#
# `--break-system-packages` is required on trixie: PEP 668 marks the system
# Python as externally managed and pip refuses to install into it otherwise.
# That is the correct flag here rather than a virtualenv, because the
# generator resolves `mkdocs` from PATH as the `node` user and a venv would
# need activating in a shell this process never starts.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends libsqlite3-dev python3 python3-pip && \
    pip3 install --no-cache-dir --break-system-packages mkdocs-techdocs-core && \
    rm -rf /var/lib/apt/lists/*
USER node
WORKDIR /app
COPY --from=build --chown=node:node /app/.yarn ./.yarn
COPY --from=build --chown=node:node /app/.yarnrc.yml  ./
COPY --from=build --chown=node:node /app/backstage.json ./
COPY --from=build --chown=node:node /app/yarn.lock /app/package.json /app/packages/backend/dist/skeleton/ ./
RUN --mount=type=cache,target=/home/node/.cache/yarn,sharing=locked,uid=1000,gid=1000 \
    yarn workspaces focus --all --production && rm -rf "$(yarn cache clean)"
COPY --from=build --chown=node:node /app/packages/backend/dist/bundle/ ./
COPY --chown=node:node app-config*.yaml ./
COPY --chown=node:node catalog-info.yaml ./
# This app's own TechDocs source. Its entity lives at the image root, so
# `backstage.io/techdocs-ref: dir:.` resolves to /app -- mkdocs.yml and docs/
# have to be here, not under ./catalog like the eight staged components.
COPY --chown=node:node mkdocs.yml ./
COPY --chown=node:node docs ./docs
COPY --chown=node:node examples ./examples
# The software catalog, static and read from this image's own filesystem --
# no git discovery, this repo has no remote. scripts/rebuild-and-deploy.sh
# stages this directory (mirroring the real repo's component paths) into
# platform/backstage/.catalog-stage/ before this build and removes it after;
# see that script's own comment on the "backstage" branch.
# `app-config.production.yaml`'s `catalog.locations` reads it back out at
# ./catalog/...
COPY --chown=node:node .catalog-stage ./catalog

# `GIT_SHA` is passed by scripts/rebuild-and-deploy.sh as `--build-arg
# GIT_SHA=$(git rev-parse HEAD)` for this component only, and stamped into a
# label `tests/e2e/test_backstage_image_matches_head.py` reads back with
# `docker image inspect` and compares against the checkout's own `git
# rev-parse HEAD` -- catching a running image that predates the commit it is
# supposed to be serving, something neither an RBAC matrix nor a healthy pod
# can see on their own. Placed here, after every COPY/RUN in this stage, so
# changing it invalidates only this one metadata layer, not the expensive
# yarn-install/tsc/build layers above -- it must still change on every
# commit, or the check it backs is worthless.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

ENV NODE_ENV=production
EXPOSE 7007
CMD ["node", "packages/backend", "--config", "app-config.yaml", "--config", "app-config.production.yaml"]
