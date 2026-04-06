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
