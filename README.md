# fcc-cloud-project

> **Cloud-Deployed Static Portfolio with CI/CD and Monitoring**  
> A hands-on AWS cloud project covering all 5 FCC CIE syllabus units.

[![Deploy to S3](https://github.com/YOUR_USERNAME/fcc-cloud-project/actions/workflows/deploy.yml/badge.svg)](https://github.com/YOUR_USERNAME/fcc-cloud-project/actions/workflows/deploy.yml)

---

## 🌐 Live Site

| Page | URL |
|------|-----|
| Home | `http://YOUR-BUCKET.s3-website.REGION.amazonaws.com/home.html` |
| About | `http://YOUR-BUCKET.s3-website.REGION.amazonaws.com/about.html` |
| Projects | `http://YOUR-BUCKET.s3-website.REGION.amazonaws.com/projects.html` |
| Contact | `http://YOUR-BUCKET.s3-website.REGION.amazonaws.com/index.html` |
| API (Render) | `https://fcc-cloud-project-11.onrender.com/api/health` |

---

## 🏗️ Architecture

```
User Browser
│
├── Static Frontend (S3 Static Website)
│   ├── home.html · about.html · projects.html · index.html · admin.html
│   └── Deployed via GitHub Actions on every push to main
│
├── HTTPS → EC2 (Amazon Linux 2023, t2.micro)
│   ├── Nginx reverse proxy (port 80/443)
│   ├── Gunicorn WSGI server (port 8000)
│   └── Flask API (app.py)
│       ├── POST /api/contact         → PostgreSQL submission
│       ├── POST /api/upload-doc      → S3 bucket (AES-256)
│       ├── POST /api/admin/login     → signed bearer token
│       └── GET/DELETE /api/admin/*   → protected admin routes
│
├── S3 Bucket        (PDF uploads, SSE-AES256, upload/ prefix)
├── PostgreSQL/SQLite (submission records)
├── IAM Role         (EC2 instance role, least-privilege)
├── VPC              (CIDR 10.0.0.0/16, public subnet 10.0.1.0/24)
└── CloudWatch       (metrics, logs, $5 billing alarm)
```

---

## 📚 Syllabus Unit Mapping

### Unit 1 — Linux & Virtualization

**What was built:** EC2 instance running Amazon Linux 2023 hosts the Flask API.

| Task | Evidence |
|------|----------|
| Linux file system operations | `sudo cp`, `chown`, `chmod 600` during setup |
| User management | `useradd -r -s /bin/false portfolio` (restricted system user) |
| Process management | Gunicorn worker processes managed by systemd |
| SSH access | EC2 key-pair auth (`~/.ssh/id_rsa`), `ssh -i key.pem ec2-user@IP` |
| Service management | `systemctl enable/start portfolio.service` |
| Troubleshooting | Used AI to fix Nginx 502 Bad Gateway (Gunicorn socket path error) |

**Key files:** [`FCC CIE/deploy/portfolio.service`](FCC%20CIE/deploy/portfolio.service), [`FCC CIE/deploy/nginx.conf`](FCC%20CIE/deploy/nginx.conf)

---

### Unit 2 — Networking

**What was built:** Custom VPC with public subnet, Internet Gateway, and Security Groups.

| Concept | Implementation |
|---------|----------------|
| IP addressing | VPC CIDR `10.0.0.0/16`, public subnet `10.0.1.0/24` |
| Public vs private IPs | EC2 gets Elastic IP; RDS in private subnet (no public IP) |
| Ports & protocols | SG allows TCP 22 (SSH), 80 (HTTP), 443 (HTTPS) — all others denied |
| DNS | Route 53 A record → EC2 Elastic IP (or S3 website endpoint) |
| Network troubleshooting | `curl -v`, `ss -tulnp`, `sudo nginx -t` used during setup |

**Key files:** [`FCC CIE/deploy/nginx.conf`](FCC%20CIE/deploy/nginx.conf)

---

### Unit 3 — Cloud Fundamentals

**What was built:** Compared deployment models and applied shared responsibility.

| Topic | What Was Done |
|-------|---------------|
| Deployment models | Public cloud (AWS) — no private hardware involved |
| Service models | EC2 = **IaaS** (we manage OS+apps), Render = **PaaS**, Gmail = **SaaS** |
| Shared responsibility | AWS secures hardware/hypervisor; we secure OS, SG rules, app code |
| Elasticity | Static frontend is stateless → can scale horizontally at zero cost |
| Azure comparison | EC2 ↔ Azure VM; S3 ↔ Azure Blob; IAM ↔ Azure AD + RBAC |

**See:** [`FCC CIE/frontend/about.html`](FCC%20CIE/frontend/about.html) — full mapping table rendered in the browser.

---

### Unit 4 — Core AWS Services

**What was built:** Used EC2, S3, IAM, VPC, and CloudWatch in production.

#### EC2
- Instance type: `t2.micro` (Free Tier eligible)
- AMI: Amazon Linux 2023
- SSH key-pair authentication
- Elastic IP for static public address

#### S3
- **Bucket 1:** Static website hosting (frontend HTML/CSS/JS)  
  - Policy: `FCC CIE/deploy/s3-bucket-policy.json` (public read)
- **Bucket 2:** PDF upload storage  
  - Server-side encryption: AES-256  
  - Access: EC2 IAM role only (no public access)

#### IAM
- EC2 **instance role** with inline policy scoped to `arn:aws:s3:::BUCKET/uploads/*`
- GitHub Actions **IAM user** with `s3:PutObject`, `s3:DeleteObject`, `cloudfront:CreateInvalidation`
- Policy file: `FCC CIE/deploy/iam-policy.json`

#### VPC
- CIDR: `10.0.0.0/16`
- Public subnet: `10.0.1.0/24` (EC2 lives here)
- Internet Gateway + route table `0.0.0.0/0 → IGW`
- Security Group: TCP 22, 80, 443 inbound; all outbound allowed

#### CloudWatch
- CloudWatch Agent on EC2: CPU, memory, disk metrics
- Log groups: `/portfolio/nginx/access`, `/portfolio/app`
- Billing alarm: SNS email alert when monthly cost > $5
- Config: `FCC CIE/deploy/cloudwatch-agent.json`

---

### Unit 5 — DevOps, Git & Monitoring

**What was built:** GitHub repo with CI/CD and CloudWatch monitoring.

#### Git Workflow
```bash
git checkout -b feature/portfolio-pages   # Create feature branch
git add .
git commit -m "feat: add home/about/projects pages with nav"
git push origin feature/portfolio-pages
# → Open PR → Review → Merge to main → CI/CD triggers
```

#### GitHub Actions CI/CD
File: [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml)

On every push to `main` that touches `FCC CIE/frontend/**`:
1. Checkout code
2. Configure AWS credentials from **GitHub Secrets** (never hardcoded)
3. `aws s3 sync frontend/ s3://BUCKET/ --delete`
4. `aws cloudfront create-invalidation --paths "/*"` (if CloudFront is configured)

**Required GitHub Secrets:**
| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM user access key |
| `AWS_SECRET_ACCESS_KEY` | IAM user secret key |
| `AWS_REGION` | e.g. `ap-south-1` |
| `S3_BUCKET` | Bucket name for static site |
| `CLOUDFRONT_DISTRIBUTION_ID` | Optional CloudFront distribution |

#### Monitoring
- CloudWatch dashboard: EC2 CPU utilization, network in/out
- Log Insights query: `filter @message like "ERROR"` for API errors
- Billing alarm: triggers when estimated charges > $5/month

---

## 🚀 Local Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/fcc-cloud-project.git
cd fcc-cloud-project

# 2. Create .env from template
cp "FCC CIE/.env.example" "FCC CIE/.env"
# Edit .env with your local config

# 3. Install Python dependencies
cd "FCC CIE"
pip install -r requirements.txt

# 4. Run the Flask API locally
python app.py
# API runs on http://localhost:5000

# 5. Open the frontend
# Open FCC CIE/frontend/home.html in your browser
# Or serve it with: python -m http.server 8080 --directory "FCC CIE/frontend"
```

---

## ☁️ AWS Deployment

### Step 1: S3 Static Website Hosting

```bash
# Create bucket
aws s3 mb s3://YOUR-BUCKET-NAME --region YOUR-REGION

# Enable static website hosting
aws s3 website s3://YOUR-BUCKET-NAME \
  --index-document home.html \
  --error-document home.html

# Apply public read policy
aws s3api put-bucket-policy \
  --bucket YOUR-BUCKET-NAME \
  --policy file://"FCC CIE/deploy/s3-bucket-policy.json"

# Deploy frontend
aws s3 sync "FCC CIE/frontend/" s3://YOUR-BUCKET-NAME/ --acl public-read
```

### Step 2: EC2 API Server

See full instructions in [`FCC CIE/deploy/README.md`](FCC%20CIE/deploy/README.md).

### Step 3: GitHub Actions

1. Go to **Settings → Secrets and variables → Actions**
2. Add the 4 secrets listed above
3. Push to `main` — the workflow deploys automatically

---

## 📁 Repository Structure

```
fcc-cloud-project/
├── .github/
│   └── workflows/
│       └── deploy.yml          ← GitHub Actions CI/CD
├── FCC CIE/
│   ├── frontend/
│   │   ├── home.html           ← Unit 3 (Cloud fundamentals)
│   │   ├── about.html          ← Unit 3 (Syllabus mapping + arch)
│   │   ├── projects.html       ← Unit 4 (AWS services)
│   │   ├── index.html          ← Contact form (Unit 5 API)
│   │   ├── admin.html          ← Admin dashboard
│   │   └── config.js           ← API base URL config
│   ├── deploy/
│   │   ├── README.md           ← EC2 setup guide (Unit 1, 2, 4)
│   │   ├── portfolio.service   ← systemd unit (Unit 1)
│   │   ├── nginx.conf          ← Reverse proxy (Unit 2)
│   │   ├── iam-policy.json     ← IAM policy (Unit 4)
│   │   ├── s3-bucket-policy.json ← S3 public policy (Unit 4)
│   │   └── cloudwatch-agent.json ← Monitoring config (Unit 5)
│   ├── app.py                  ← Flask API backend
│   ├── config.py               ← App configuration
│   └── requirements.txt        ← Python dependencies
├── netlify.toml                ← Netlify fallback hosting config
└── README.md                   ← This file
```

---

## 🔐 Security Notes

- No credentials are hardcoded anywhere — all secrets via environment variables or GitHub Secrets
- IAM uses least-privilege: EC2 role can only read/write `uploads/` prefix in the uploads bucket
- S3 static site bucket has public read; upload bucket has **no public access**
- CORS is restricted to the specific frontend origin
- Rate limiting: 5 login attempts/min, 10 contact submissions/min
- Tokens expire after 1 hour (HMAC-SHA256 signed)

---

*FCC CIE Cloud Computing Project — demonstrating Units 1–5 of the syllabus through a working AWS deployment.*
