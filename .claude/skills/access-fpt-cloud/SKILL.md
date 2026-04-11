---
name: access-fpt-cloud
description: Connect to the FPT Cloud serverless GPU (2x H100 80GB) via SSH for quick GPU jobs.
---

# FPT Cloud Serverless GPU (2x NVIDIA H100 80GB)

## When to use

Use for quick jobs — short experiments, verifying a single result, or testing a small script on GPU hardware.

## Automated management via `fpt_manage.py`

All commands use Playwright for browser automation. SSH keys are stored in `.ssh/` within this skill directory.

### Prerequisites

```bash
# From this skill's directory:
cd .claude/skills/access-fpt-cloud
uv sync          # install playwright
uv run playwright install chromium   # install browser
```

### Commands

```bash
# Login (required first)
python fpt_manage.py login <username> <password>

# Apply referral code for free credits
python fpt_manage.py apply-referral

# Create a GPU container (generates SSH key in .ssh/ if needed)
python fpt_manage.py create [--gpu 1|2|3|4] [--template pytorch|jupyter|ubuntu]

# List running containers
python fpt_manage.py list

# Get SSH command for the current container
python fpt_manage.py ssh-cmd

# Test SSH + nvidia-smi
python fpt_manage.py test-ssh

# Delete a container
python fpt_manage.py delete [container_name]
```

### Typical workflow

```bash
python fpt_manage.py login <user> <pass>
python fpt_manage.py create --gpu 2 --template pytorch
python fpt_manage.py test-ssh
# ... do work ...
python fpt_manage.py delete
```

## Manual account creation

Account signup has reCAPTCHA and must be done manually:

1. Go to https://ai.fptcloud.com
2. Click **Sign in/Sign up** → **Continue with FPT ID** → **Sign up**
3. Fill in the form and create the account.
4. After logging in, go to **Billing** and apply referral code: `SIVAREDDYPROF-BNI8WX1W5X`

## Quick connect (if you already know the port)

```bash
ssh root@tcp-endpoint.serverless.fptcloud.com -p <PORT> \
    -i .claude/skills/access-fpt-cloud/.ssh/id_ed25519 \
    -o ProxyJump=none -o StrictHostKeyChecking=no
```

## Container options

| GPU | Cost | CPU | RAM | Temp Disk |
|-----|------|-----|-----|-----------|
| 1xH100 | $2.54/hr | 15 | 250 GB | 1000 GB |
| 2xH100 | $5.08/hr | 30 | 500 GB | 2000 GB |
| 3xH100 | $7.62/hr | 45 | 750 GB | 3000 GB |
| 4xH100 | $10.16/hr | 60 | 1000 GB | 4000 GB |

Templates: `pytorch`, `jupyter`, `ubuntu`, `tensorflow`, `cuda`, `vllm`, `ollama`, `code-server`
