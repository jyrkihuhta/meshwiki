FROM e2bdev/code-interpreter:latest

# Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - \
    && sudo apt-get install -y nodejs

# Kilo CLI
RUN sudo npm install -g @kilocode/cli

# gh CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list \
    && sudo apt-get update -q \
    && sudo apt-get install -y gh

# Python packages the grinder always needs
RUN pip install --no-cache-dir \
    httpx \
    black \
    isort \
    ruff \
    pytest \
    pytest-asyncio \
    pytest-cov

# Pre-bake meshwiki + orchestrator Python dependencies so runtime pip installs are fast.
# Clone, install deps (non-editable), delete clone. The runtime `pip install -e .` will
# skip downloading packages that are already installed.
ARG GITHUB_TOKEN
RUN git clone --depth 1 https://x-access-token:${GITHUB_TOKEN}@github.com/jyrkihuhta/meshwiki.git /tmp/meshwiki-prebake \
    && pip install --no-cache-dir /tmp/meshwiki-prebake \
    && pip install --no-cache-dir /tmp/meshwiki-prebake/orchestrator \
    && rm -rf /tmp/meshwiki-prebake
