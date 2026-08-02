#!/usr/bin/env bash
# Fast build + deploy for the keeply k3d cluster.
#
# Replaces `docker build && k3d image import && kubectl rollout restart`,
# which was costing ~9 min of `next build` plus ~4 min of tarball
# export/import per UI change, and starving the host of memory.
#
# What makes it fast:
#   * BuildKit cache mounts (in the Dockerfiles) keep npm's cache and
#     webpack's incremental cache between builds
#   * a local registry the cluster pulls from, so only changed layers move
#     — `k3d image import` re-ships the whole image every time
#   * capped build parallelism, so the machine stays usable
#
# Usage:
#   k8s/deploy.sh ui        # keep-ui only (most common)
#   k8s/deploy.sh aiops     # aiops-api + mcp-gateway
#   k8s/deploy.sh api       # keep-backend
#   k8s/deploy.sh all
set -euo pipefail

REGISTRY="${KEEPLY_REGISTRY:-k3d-myregistry.localhost:5050}"
# The cluster resolves the registry by its in-network name and port 5000;
# the host publishes it on 5050. Same registry, two addresses.
CLUSTER_REGISTRY="${KEEPLY_CLUSTER_REGISTRY:-k3d-myregistry.localhost:5000}"
NAMESPACE="${KEEPLY_NAMESPACE:-keeply}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Leave cores for the desktop; the default (one worker per core) is what
# made 28-core machines thrash.
BUILD_CPUS="${KEEPLY_BUILD_CPUS:-6}"
BUILD_MEMORY="${KEEPLY_BUILD_MEMORY:-8g}"

export DOCKER_BUILDKIT=1

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

build_and_push() {
  local name="$1" dockerfile="$2" context="$3" deployments="$4"
  local image="${REGISTRY}/${name}:dev"

  log "building ${name}"
  docker build \
    --cpuset-cpus "0-$((BUILD_CPUS - 1))" \
    --memory "${BUILD_MEMORY}" \
    -f "${dockerfile}" -t "${image}" "${context}"

  log "pushing ${name} (only changed layers move)"
  docker push "${image}"

  for deployment in ${deployments}; do
    log "rolling out ${deployment}"
    # setImage first so the pod pulls the new digest; :dev is a moving tag
    # so imagePullPolicy must be Always on these deployments.
    kubectl -n "${NAMESPACE}" set image "deployment/${deployment}" \
      "$(kubectl -n "${NAMESPACE}" get "deployment/${deployment}" \
          -o jsonpath='{.spec.template.spec.containers[0].name}')=${CLUSTER_REGISTRY}/${name}:dev" \
      >/dev/null
    kubectl -n "${NAMESPACE}" rollout restart "deployment/${deployment}" >/dev/null
  done

  for deployment in ${deployments}; do
    kubectl -n "${NAMESPACE}" rollout status "deployment/${deployment}" --timeout=300s
  done
}

target="${1:-ui}"

case "${target}" in
  ui)
    build_and_push keep-ui "${ROOT}/keep-ui/Dockerfile.keeply" "${ROOT}/keep-ui" "keep-frontend"
    ;;
  aiops)
    build_and_push keep-aiops "${ROOT}/keep-aiops/Dockerfile" "${ROOT}/keep-aiops" "aiops-api mcp-gateway"
    ;;
  api)
    build_and_push keep-api "${ROOT}/docker/Dockerfile.api.local" "${ROOT}" "keep-backend"
    ;;
  all)
    "$0" api
    "$0" aiops
    "$0" ui
    ;;
  *)
    echo "usage: $0 {ui|aiops|api|all}" >&2
    exit 1
    ;;
esac

log "done"
