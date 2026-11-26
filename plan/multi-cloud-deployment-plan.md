# Vault City AI: Multi-Cloud Production Deployment Plan

This updated deployment architecture meets all of your specific production requirements:
1. **Frontend:** Hosted on **Cloudflare Pages** (Global Edge CDN, automatic SSL, zero server load on EC2).
2. **Object Storage:** **Cloudflare R2** (S3-compatible, zero egress fees, replaces MinIO).
3. **Core Stack (FastAPI, PostgreSQL 16 + pgvector, Redis):** Containerized on **AWS EC2**, designed to be **transferred to any fresh EC2 instance in under 3 minutes**.
4. **Models & Ingestion Inference:** Hosted on an **Oracle Cloud Free Instance** (1 vCPU, 1 GB RAM or 24 GB Ampere Free Tier), connected securely to EC2 via **Cloudflare Tunnel**.

---

## 1. High-Level Architecture

```mermaid
flowchart TD
    subgraph Client ["Client Layer"]
        User["User Browser / Mobile PWA"]
    end

    subgraph CloudflareEdge ["Cloudflare Edge (Global CDN & Storage)"]
        CF_Pages["Cloudflare Pages\n(Vite PWA Build - Static Assets)"]
        CF_DNS["Cloudflare DNS & Proxy\n(SSL Termination & WAF)"]
        
        subgraph R2Storage ["Cloudflare R2 (S3-Compatible, 0 Egress)"]
            R2Quarantine[("R2: vault-quarantine")]
            R2Secured[("R2: vault-secured")]
            R2Backups[("R2: vault-db-backups")]
        end
    end

    subgraph AWSEC2 ["AWS EC2 (Transferable Core Instance)"]
        subgraph DockerCore ["Docker Compose Stack (vault_prod_net)"]
            FastAPI["FastAPI Backend Container\n(:8000)"]
            PostgresDB[("PostgreSQL 16 + pgvector\n(vault_postgres_prod_data)")]
            RedisCache[("Redis 7 Cache\n(vault_redis_prod_data)")]
        end
    end

    subgraph OracleCloud ["Oracle Cloud Always Free Instance (Models Node)"]
        CFTunnel["Cloudflare Tunnel (cloudflared)\n(Zero open firewall ports)"]
        subgraph ModelStack ["Model & Scan Services"]
            ClamAV["ClamAV Daemon / Scanner\n(Antivirus)"]
            MLVector["FastEmbed & Security Models\n(ONNX CPU Inference)"]
        end
    end

    User -->|1. Loads App UI| CF_Pages
    User -->|2. API Requests /api/*| CF_DNS
    CF_DNS -->|3. Proxy to EC2 IP:80/443| FastAPI
    
    FastAPI -->|4. Reads/Writes| PostgresDB
    FastAPI -->|4. Caches| RedisCache
    
    FastAPI -->|5. Uploads / Downloads (boto3)| R2Quarantine
    FastAPI -->|5. Promotes Verified Files| R2Secured
    PostgresDB -.->|Daily Auto-Dump| R2Backups

    FastAPI -->|6. Scan & Vectorize Requests| CFTunnel
    CFTunnel --> ModelStack
```

---

## 2. Cloudflare Layer (Frontend & R2 Storage)

### A. Frontend on Cloudflare Pages
* **Cost:** 100% Free.
* **Benefits:** Unlimited bandwidth, global 300+ city edge caching, instantaneous PWA loads, automatic HTTPS.
* **Deployment:**
  1. Push code to GitHub (or deploy via Wrangler CLI).
  2. In Cloudflare Dashboard $\rightarrow$ **Workers & Pages** $\rightarrow$ **Create Application** $\rightarrow$ **Pages**.
  3. Set Build Settings:
     - **Build command:** `cd frontend && npm run build`
     - **Build output directory:** `frontend/dist`
  4. Environment Variables in Pages:
     - `VITE_API_URL`: `https://api.yourdomain.com` (or leave empty if using Cloudflare routing rules).

### B. Storage on Cloudflare R2 (Replaces MinIO)
Cloudflare R2 is 100% S3-compatible and has **zero egress fees** (unlike AWS S3, where downloading user files costs bandwidth fees).
* **Setup in Cloudflare Dashboard:**
  1. Go to **R2 Object Storage** $\rightarrow$ Create two buckets:
     - `vault-quarantine`
     - `vault-secured`
     - `vault-db-backups` (for instant EC2 migration snapshots)
  2. Create an **R2 API Token** with `Object Read & Write` permissions.
  3. Cloudflare gives you:
     - `Account ID`
     - `Access Key ID`
     - `Secret Access Key`
     - `Endpoint URL`: `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`
* **Zero Code Changes in Backend:**
  Our `backend/app/services/s3_service.py` uses `boto3`. In `.env.prod`, simply provide:
  ```ini
  MINIO_ENDPOINT_URL=https://<YOUR_ACCOUNT_ID>.r2.cloudflarestorage.com
  MINIO_ACCESS_KEY=<R2_ACCESS_KEY_ID>
  MINIO_SECRET_KEY=<R2_SECRET_ACCESS_KEY>
  MINIO_REGION=auto
  S3_QUARANTINE_BUCKET=vault-quarantine
  S3_SECURED_BUCKET=vault-secured
  ```

---

## 3. AWS EC2 Core Stack: "Transfer in Minutes" Blueprint

To guarantee that your backend, database, and cache can be migrated to **any new AWS EC2 instance in under 3 minutes**, the deployment is fully decoupled from the machine's local disk.

### The 3 Pillars of Instant Portability:
1. **Stateless Compute:** All runtime configuration lives in `.env.prod`. No custom software is compiled on the host.
2. **Single-Command Docker Stack:** `docker-compose.prod.yml` runs PostgreSQL (`pgvector`), Redis, and FastAPI in a self-healing private network.
3. **Automated R2 Database Sync:**
   - Database backups are streamed directly to Cloudflare R2 every 6 hours.
   - When launching a brand new EC2, a 1-line script pulls the latest snapshot from R2 and restores PostgreSQL in seconds.

### The 3-Minute EC2 Migration Workflow

#### Step 1: Launch Any Clean EC2 Instance
- OS: Ubuntu 22.04 / 24.04 LTS (e.g. `t3.small`, `t4g.small`, or any size).
- Security Group: Allow ports `80`, `443`, and `22`.

#### Step 2: Run the One-Click Bootstrap Script (`bootstrap-ec2.sh`)
```bash
#!/bin/bash
set -e

echo "=== [1/4] Installing Docker & Docker Compose ==="
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker ubuntu

echo "=== [2/4] Cloning Repository ==="
git clone https://github.com/your-org/vaultcity.ai.git /opt/vaultcity
cd /opt/vaultcity

echo "=== [3/4] Fetching Secrets & Configuration ==="
# Retrieve .env.prod securely from AWS Secrets Manager or copy your saved .env.prod
# aws secretsmanager get-secret-value --secret-id vault-prod-env --query SecretString --output text > .env.prod

echo "=== [4/4] Launching Stack & Restoring Latest DB Backup ==="
docker compose -f docker-compose.prod.yml up -d
sleep 5
./scripts/restore-from-r2.sh

echo "=== Vault City AI is LIVE on this EC2 instance! ==="
```

#### Step 3: Switch DNS in Cloudflare
- In Cloudflare DNS, change the `A` record of `api.yourdomain.com` to the new EC2's public IP.
- Traffic cuts over immediately with **zero downtime**.

---

## 4. Oracle Cloud Free Instance (Models & Inference Node)

You requested running the models in an **Oracle Cloud Free 1 vCPU, 1 GB RAM instance**.

### Important Engineering Realities & Optimization:

> [!WARNING]
> **Memory Bottleneck on 1 GB RAM:**
> A standard Linux OS + Docker uses ~250 MB RAM. 
> - **ClamAV:** Loading the standard virus definitions into memory requires ~700 MB - 1 GB RAM.
> - **FastEmbed / ONNX models:** Requires ~200 - 300 MB RAM.
> 
> If both run at the same time on 1 GB RAM without optimization, the Linux kernel will trigger an **OOM (Out Of Memory) crash**.

### The Solution for 1 GB RAM:
1. **Enable 2 GB Compressed Swap (zram + swapfile):**
   ```bash
   sudo fallocate -l 2G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```
2. **Run ClamAV in Lightweight On-Demand Mode:**
   Instead of keeping `clamd` permanently in RAM, run a lightweight REST API that scans via streams or uses the lightweight signature database.
3. **Run ONNX Runtime in Single-Thread CPU Mode:**
   Restrict ONNX runtime to 1 thread (`OMP_NUM_THREADS=1`) so CPU usage does not stall.

> [!TIP]
> **Oracle Cloud Always Free "Secret" Upgrade (4 Cores & 24 GB RAM):**
> Every Oracle Cloud account is entitled to the **Ampere A1 (Arm64)** shape **100% Free Forever**:
> - **4 OCPUs (vCPUs)**
> - **24 GB RAM**
> - **200 GB Storage**
> 
> If you create an **Ampere A1** instance instead of the 1 GB AMD instance, you get **24 GB RAM for \$0/month**, allowing you to run ClamAV, FastEmbed, ONNX models, and even Ollama VLMs at full speed without worrying about memory limits.

---

## 5. Private Inter-Cloud Networking: Cloudflare Tunnel

To connect your **AWS EC2 backend** to the **Oracle Cloud Model instance**:
- **Do NOT open port 3001 or 3310 to the public internet** on the Oracle instance.
- Instead, install **Cloudflare Tunnel (`cloudflared`)** on the Oracle instance.

```mermaid
flowchart LR
    EC2Backend["FastAPI Backend (AWS EC2)"] -->|HTTPS (Cloudflare Token)| CFEdge["Cloudflare Edge"]
    CFEdge -->|Encrypted Tunnel (No open ports)| OracleTunnel["cloudflared daemon (Oracle Cloud)"]
    OracleTunnel --> ModelAPI["Model Service (:3001)"]
```

### Setup on Oracle Instance:
1. In Cloudflare Zero Trust $\rightarrow$ **Networks** $\rightarrow$ **Tunnels** $\rightarrow$ Create Tunnel `vault-model-node`.
2. Run the provided 1-line installation command on the Oracle VM:
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   sudo cloudflared service install <TUNNEL_TOKEN>
   ```
3. Map the tunnel hostname in Cloudflare:
   - Hostname: `models-internal.yourdomain.com` $\rightarrow$ Points to `http://localhost:3001`.
4. In your EC2 `.env.prod`:
   ```ini
   SAM_API_URL=https://models-internal.yourdomain.com
   ```
   Now the AWS EC2 backend talks to the Oracle model instance over an encrypted, authenticated tunnel with **zero open firewall ports**.

---

## 6. Implementation Checklist & Deliverables

| Step | Action | Status / File |
| :--- | :--- | :--- |
| **1. Frontend** | Deploy `frontend/dist` to Cloudflare Pages | Ready (`npm run build`) |
| **2. Storage** | Create R2 buckets (`quarantine`, `secured`, `backups`) | Cloudflare Dashboard |
| **3. EC2 Core** | Launch `docker-compose.prod.yml` (Postgres + pgvector, Redis, FastAPI) | [docker-compose.prod.yml](file:////wsl.localhost/Ubuntu/home/meritech/Manas_research/Vaultcity.ai/docker-compose.prod.yml) |
| **4. Portability** | Add automated R2 backup/restore scripts | `scripts/backup-to-r2.sh`, `scripts/restore-from-r2.sh` |
| **5. Oracle Models**| Deploy lightweight model microservice + Cloudflare Tunnel | `lambdas/file_scanner_extractor` |
| **6. DNS** | Point Cloudflare DNS to Pages & EC2 IP | Cloudflare Dashboard |
