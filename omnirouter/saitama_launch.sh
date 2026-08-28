#!/bin/bash
# Durable launcher for the Saitama Omnirouter gateway (persistent tmux).
# Reads provider keys ONLY from the 0600 .env; never writes secrets to logs/git.
# V5.4: exports GROQ_API_KEY alongside OPENROUTER_API_KEY (Groq BYOK STT passthrough).
set -e
cd /workspace/omnirouter
export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' .env | cut -d= -f2-)"
export GROQ_API_KEY="$(grep -E '^GROQ_API_KEY=' .env | cut -d= -f2-)"
.venv/bin/python saitama_gateway.py 2>&1 | tee /workspace/omnirouter/gateway.log
