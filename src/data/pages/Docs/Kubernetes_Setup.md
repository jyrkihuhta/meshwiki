---
title: Kubernetes Setup
tags:
  - infrastructure
  - kubernetes
  - devops
status: active
author: alice
priority: medium
---

# Kubernetes Setup

MeshWiki runs on a local k3d Kubernetes cluster with Istio service mesh and Flux GitOps.

## Components

| Component | Purpose | Access |
|-----------|---------|--------|
| k3d | Local k8s cluster | CLI |
| Istio | Service mesh + ingress | Automatic |
| Rancher | Cluster management UI | https://rancher.localhost:8443 |
| Flux | GitOps CD | Automatic |

## Quick Start

```bash
# Provision infrastructure
cd infra/local && terraform apply

# Build and deploy
docker build -t meshwiki:latest .
k3d image import meshwiki:latest -c meshwiki
kubectl rollout restart deployment/meshwiki -n meshwiki
```

## Cluster Architecture

```
k3d Cluster "meshwiki"
├── 1 Server Node (control plane)
├── 2 Agent Nodes (workers)
├── Istio Ingress Gateway
│   ├── wiki.localhost:8080 → MeshWiki
│   ├── rancher.localhost:8443 → Rancher
│   └── test.localhost:8080 → Test App
└── Flux Controllers
    └── Watches deploy/apps/ in git
```

## Deployment Manifests

Located in `deploy/apps/meshwiki/`:

- `namespace.yaml` - meshwiki namespace
- `deployment.yaml` - Pod spec with data volume
- `service.yaml` - ClusterIP on port 80
- `pvc.yaml` - PersistentVolumeClaim for wiki data
- `virtualservice.yaml` - Istio routing

## Dockerfile

Multi-stage build at repo root:

1. **Stage 1** (`rust-builder`): Compiles `graph_core` wheel with Maturin
2. **Stage 2** (runtime): Installs Python deps + graph_core, copies app

## Troubleshooting

### Pods not starting

```bash
kubectl get pods -n meshwiki
kubectl describe pod <pod-name> -n meshwiki
kubectl logs -f deployment/meshwiki -n meshwiki
```

### Force Flux sync

```bash
flux reconcile kustomization apps --with-source
```

### Image not found

Remember: local images must be imported to k3d:

```bash
k3d image import meshwiki:latest -c meshwiki
```

## Related

- [[Docs/Architecture Overview]] - Application architecture
- [[Project Roadmap]] - Development timeline
- [[Docs/Python Development]] - Local dev setup
