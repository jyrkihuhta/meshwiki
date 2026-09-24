# Getting Started

This guide covers setting up MeshWiki for local development and deploying to Kubernetes.

## Prerequisites

### For local development

- [Python](https://www.python.org/) >= 3.12
- [Rust](https://rustup.rs/) — for the graph engine (optional, but recommended)
- [Maturin](https://www.maturin.rs/) — Python/Rust build tool (installed automatically by `dev.sh`)

### For Kubernetes deployment

- [Docker](https://docs.docker.com/get-docker/) — Container runtime
- [k3d](https://k3d.io/) — Local Kubernetes in Docker
- [kubectl](https://kubernetes.io/docs/tasks/tools/) — Kubernetes CLI
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- [Flux CLI](https://fluxcd.io/flux/installation/) — GitOps toolkit

## Quick Start (Local Development)

### Recommended: With Rust graph engine

The `dev.sh` script builds the Rust graph engine, installs dependencies, and starts the server:

```bash
# Clone the repository
git clone https://github.com/jyrkihuhta/meshwiki.git
cd meshwiki

# Build Rust engine + start server
./dev.sh
```

Access at http://localhost:8000

`dev.sh` options:
```bash
./dev.sh                 # Build Rust engine + start server
./dev.sh --skip-build    # Start server without rebuilding Rust
./dev.sh --build-only    # Build Rust engine only
```

### Without the Rust engine

If you just want to work on the wiki without graph features (backlinks, MetaTable queries, graph visualization), you can skip the Rust build entirely:

```bash
pip install -e .          # from the repo root (pyproject.toml lives there)
cd src
uvicorn meshwiki.main:app --reload
```

The app runs normally — graph features gracefully degrade to empty/unavailable.

### Building the Rust engine manually

If you need to build the graph engine separately:

```bash
cd graph-core
source ~/.cargo/env          # Ensure Rust is in PATH
python -m venv .venv && source .venv/bin/activate
pip install maturin

# Python 3.14+ requires this flag
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
```

### Running tests

```bash
# Python integration tests (59 tests)
pip install -e ".[dev]"   # from the repo root (pyproject.toml lives there)
cd src
pytest tests/ -v

# Rust graph engine tests (70 tests)
cd graph-core
source .venv/bin/activate
python -m pytest tests/ -v

# With coverage
cd src && pytest --cov=meshwiki
```

## Full Kubernetes Deployment

### Step 1: Create k3d Cluster

```bash
cd infra/local

# Initialize Terraform
terraform init

# Create cluster with Rancher and Istio
terraform apply
```

This creates:
- k3d cluster with 1 server + 2 agents
- Istio service mesh
- Rancher cluster management
- cert-manager for TLS

Wait for all pods to be ready:

```bash
kubectl get pods -A
```

### Step 2: Configure /etc/hosts

Add these entries to `/etc/hosts`:

```
127.0.0.1 rancher.localhost
127.0.0.1 wiki.localhost
127.0.0.1 test.localhost
```

### Step 3: Bootstrap Flux

Set up GitOps with your GitHub repository:

```bash
# Export GitHub token
export GITHUB_TOKEN=<your-token>

# Bootstrap Flux
flux bootstrap github \
  --owner=<your-github-username> \
  --repository=meshwiki \
  --branch=main \
  --path=deploy/flux \
  --personal
```

Flux will now automatically deploy applications from `deploy/apps/`.

### Step 4: Build and Deploy MeshWiki

```bash
# Build Docker image
cd src
docker build -t meshwiki:latest .

# Import to k3d cluster
k3d image import meshwiki:latest -c meshwiki

# Restart deployment to pick up new image
kubectl rollout restart deployment/meshwiki -n meshwiki

# Watch rollout
kubectl rollout status deployment/meshwiki -n meshwiki
```

### Step 5: Verify

- **MeshWiki:** http://wiki.localhost:8080
- **Rancher:** https://rancher.localhost:8443
- **Test App:** http://test.localhost:8080

## Development Workflow

### Making Code Changes

1. Edit code in `src/meshwiki/`
2. For local development: `./dev.sh --skip-build` (uvicorn auto-reloads)
3. For Rust changes: `./dev.sh` (rebuilds engine and restarts)
4. For k8s deployment:

```bash
# Rebuild and redeploy
cd src && docker build -t meshwiki:latest . && \
k3d image import meshwiki:latest -c meshwiki && \
kubectl rollout restart deployment/meshwiki -n meshwiki
```

### Making Kubernetes Changes

1. Edit manifests in `deploy/apps/meshwiki/`
2. Commit and push to GitHub
3. Flux automatically applies changes (within ~1 minute)

Or apply manually:

```bash
kubectl apply -k deploy/apps/meshwiki/
```

### Viewing Logs

```bash
# MeshWiki logs
kubectl logs -f deployment/meshwiki -n meshwiki

# Istio ingress logs
kubectl logs -f deployment/istio-ingress -n istio-ingress

# Flux logs
kubectl logs -f deployment/source-controller -n flux-system
```

## Project Structure

```
meshwiki/
├── dev.sh                      # Development startup script
├── graph-core/                 # Rust graph engine (petgraph + PyO3)
│   ├── Cargo.toml
│   ├── src/                    # Rust source
│   └── tests/                  # PyO3 integration tests (70 tests)
├── src/
│   ├── meshwiki/              # Python application
│   │   ├── main.py             # FastAPI routes + WebSocket
│   │   ├── core/               # Storage, parser, graph, WebSocket manager
│   │   ├── templates/          # Jinja2 templates
│   │   └── static/             # CSS + D3.js graph visualization
│   ├── tests/                  # Integration tests (59 tests)
│   ├── pyproject.toml          # Python dependencies
│   └── Dockerfile              # Container build
├── docs/                       # Documentation
│   ├── architecture.md         # System design
│   ├── prd/                    # Product requirements
│   ├── adr/                    # Architecture decisions
│   └── domains/                # Domain-specific design docs
├── deploy/
│   ├── flux/                   # Flux GitOps configuration
│   └── apps/                   # Application manifests
│       ├── meshwiki/          # MeshWiki k8s resources
│       └── test-app/           # Test application
├── infra/local/                # Terraform (k3d + Istio + Rancher)
└── data/pages/                 # Wiki content (gitignored)
```

## Troubleshooting

### Cluster Won't Start

```bash
# Check Docker is running
docker ps

# Delete and recreate cluster
k3d cluster delete meshwiki
terraform apply
```

### Can't Access Services

1. Check pods are running:
   ```bash
   kubectl get pods -A
   ```

2. Check Istio gateway:
   ```bash
   kubectl get gateway -A
   kubectl get virtualservice -A
   ```

3. Check k3d load balancer:
   ```bash
   docker ps | grep k3d
   ```

### Flux Not Deploying

```bash
# Check Flux status
flux get all

# Check kustomization
flux get kustomization

# Force reconciliation
flux reconcile kustomization apps --with-source
```

### Image Not Updating

```bash
# Verify image is in k3d
docker exec k3d-meshwiki-server-0 crictl images | grep meshwiki

# Force pod recreation
kubectl delete pod -l app=meshwiki -n meshwiki
```

### Rust Engine Build Fails

```bash
# Ensure Rust is installed
rustup --version

# Ensure correct Python version
python --version  # Must be 3.12+

# For Python 3.14+, set ABI3 compatibility flag
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
```

## Cleanup

```bash
# Delete everything
cd infra/local
terraform destroy

# Or just delete the cluster
k3d cluster delete meshwiki
```

## Next Steps

- Read the [Architecture](architecture.md) document for system design details
- Check [TODO.md](../TODO.md) for current tasks and roadmap
- Explore the [Graph Engine domain doc](domains/graph-engine.md) for Rust engine details
