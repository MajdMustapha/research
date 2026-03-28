# EC2 Deployment Guide

## Recommended Instance

| Setting | Value | Why |
|---|---|---|
| **AMI** | Ubuntu 24.04 LTS (ami search: `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*`) | Stable, Python 3.12 built-in |
| **Instance type** | `t3.micro` | 2 vCPU, 1 GB RAM — more than enough for this bot |
| **Storage** | 8 GB gp3 (default) | SQLite DB stays tiny |
| **Cost** | ~$0.0104/hr = **~$7.50/month** | Free tier eligible for 12 months |

> `t3.micro` is free-tier eligible. The bot uses <50 MB RAM and near-zero CPU (one HTTP scan per hour).

## Launch Steps

### 1. Launch EC2 instance

```
Region:          Any (us-east-1 is cheapest)
AMI:             Ubuntu 24.04 LTS
Instance type:   t3.micro
Key pair:        Create or select one
Security group:  Allow SSH (22) + TCP 8000 from your IP
```

### 2. SSH in and run setup

```bash
ssh -i your-key.pem ubuntu@<EC2-PUBLIC-IP>

# Download and run the setup script
git clone https://github.com/MajdMustapha/research.git
cd research/polymarket-system/deploy
sudo bash ec2-setup.sh
```

### 3. Edit config for your needs

```bash
sudo nano /home/polybot/polymarket-system/.env
```

Change `POLY_PRIVATE_KEY` and `POLY_FUNDER_ADDRESS` if going live.
Then restart:

```bash
sudo systemctl restart polybot
```

### 4. Verify

```bash
# Check service
systemctl status polybot

# Check logs
journalctl -u polybot -f

# Test API
curl http://localhost:8000/api/status

# Trigger a scan
curl -X POST http://localhost:8000/api/scan/trigger
```

### 5. Access dashboard

Open `http://<EC2-PUBLIC-IP>:8000` in your browser.
The dashboard HTML auto-connects to the API.

## Useful Commands

| Command | What it does |
|---|---|
| `systemctl status polybot` | Check if running |
| `journalctl -u polybot -f` | Live logs |
| `systemctl restart polybot` | Restart after config change |
| `sqlite3 /home/polybot/polymarket-system/backend/bot_data.db "SELECT * FROM trades"` | Query trades |
| `curl -X POST http://localhost:8000/api/scan/trigger` | Manual scan |

## Security Notes

- `.env` is chmod 600, only readable by `polybot` user
- Never set `DRY_RUN=false` without verifying keys and understanding the risk
- Consider adding nginx reverse proxy + HTTPS if exposing dashboard publicly
