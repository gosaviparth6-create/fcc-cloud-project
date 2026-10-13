# Portfolio API Deployment

## Configuration Files

This directory contains deployment configuration files for Amazon Linux 2023:

- `portfolio.service` - systemd unit file for Gunicorn
- `nginx.conf` - Nginx reverse proxy configuration
- `iam-policy.json` - IAM policy for AWS permissions
- `cloudwatch-agent.json` - CloudWatch agent configuration

## Prerequisites

Before deploying, ensure you have the following placeholders configured:

### Required Placeholders (Replace ALL_CAPS values)

- **PORTFOLIO_USER** - System user to run the service (e.g., `portfolio`)
- **PORTFOLIO_GROUP** - System group for the service (e.g., `portfolio`)
- **YOUR_DOMAIN.COM** - Your domain name
- **BUCKET** - S3 bucket name for uploads
- **REGION** - AWS region (e.g., `us-east-1`)
- **ACCOUNT_ID** - AWS account ID
- **SECRET_KEY** - Flask secret key for token signing
- **DATABASE_URL** - Database connection string
- **ADMIN_USERNAME** - Admin username
- **ADMIN_PASSWORD** - Admin password

---

## Installation Commands (Amazon Linux 2023)

```bash
# 1. Update system and install prerequisites
sudo dnf update -y
sudo dnf install -y python3 python3-pip python3-devel gcc openssl-devel systemd-devel

# 2. Install application dependencies
sudo pip3 install --upgrade pip
sudo pip3 install gunicorn flask flask-cors flask-limiter itsdangerous sqlalchemy gevent

# 3. Create application user and directory
sudo useradd -r -s /bin/false PORTFOLIO_USER
sudo mkdir -p /opt/portfolio
sudo chown PORTFOLIO_USER:PORTFOLIO_USER /opt/portfolio

# 4. Copy application files
sudo cp app.py /opt/portfolio/
sudo cp requirements.txt /opt/portfolio/
sudo chown -R PORTFOLIO_USER:PORTFOLIO_USER /opt/portfolio
sudo chmod 600 /opt/portfolio/app.py

# 5. Install and configure Nginx
sudo amazon-linux-extras install -y nginx1
sudo cp deploy/nginx.conf /etc/nginx/conf.d/portfolio.conf
sudo rm /etc/nginx/conf.d/default.conf
sudo systemctl enable nginx

# 6. Install systemd service
sudo cp deploy/portfolio.service /etc/systemd/system/portfolio.service
sudo systemctl daemon-reload
sudo systemctl enable portfolio.service

# 7. Configure firewall (if using firewalld)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload

# 8. Install and configure CloudWatch Agent
sudo dnf install -y amazon-cloudwatch-agent
sudo cp deploy/cloudwatch-agent.json /opt/aws/amazon-cloudwatch-agent/bin/config.json
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:/opt/aws/amazon-cloudwatch-agent/bin/config.json -s

# 9. Set environment variables (create /etc/profile.d/portfolio.sh)
sudo tee /etc/profile.d/portfolio.sh << 'EOF'
export PORTFOLIO_ENV=production
export SECRET_KEY=CHANGE_ME_IN_PRODUCTION
export DATABASE_URL=POSTGRESQL_CONNECTION_STRING
export ADMIN_USERNAME=ADMIN_USERNAME_VALUE
export ADMIN_PASSWORD=ADMIN_PASSWORD_VALUE
export ALLOWED_ORIGIN=https://YOUR_DOMAIN.COM
EOF

# 10. Start services
sudo systemctl start nginx
sudo systemctl start portfolio.service

# 11. Verify services
sudo systemctl status nginx
sudo systemctl status portfolio.service

# 12. Check logs
sudo journalctl -u portfolio.service -f
sudo tail -f /var/log/nginx/portfolio_access.log
```

## Verification

Once deployed, verify the endpoints:

```bash
# Health check
curl http://localhost/api/health

# Admin login (replace with actual credentials)
curl -X POST http://localhost/api/admin/login \
  -H "Content-Type: application/json" \
  -d '{"username":"ADMIN_USERNAME_VALUE","password":"ADMIN_PASSWORD_VALUE"}'

# Protected route (use token from login)
curl http://localhost/api/admin/dashboard \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

## SSL Certificate Setup (Optional)

For HTTPS support with Let's Encrypt:

```bash
# Install certbot
sudo dnf install -y certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d YOUR_DOMAIN.COM --email your-email@example.com --non-interactive --agree-tos

# Verify auto-renewal
sudo certbot renew --dry-run
```

## Troubleshooting

### Common Issues

1. **Permission denied on logs**: Ensure Nginx and application user have write access to `/var/log/nginx/`
   ```bash
   sudo chown -R nginx:nginx /var/log/nginx/
   sudo chmod -R 755 /var/log/nginx/
   ```

2. **Gunicorn binding error**: Check if port 8000 is available
   ```bash
   sudo ss -tulnp | grep 8000
   ```

3. **Application not starting**: Check journal logs
   ```bash
   sudo journalctl -u portfolio.service -n 50 --no-pager
   ```

4. **Nginx configuration error**: Test configuration before restarting
   ```bash
   sudo nginx -t
   ```

### Debug Mode (Temporary)

To temporarily enable debug mode for troubleshooting:

```bash
sudo systemctl edit portfolio.service
```

Add override to set `FLASK_DEBUG=1` and `FLASK_ENV=development`, then restart:
```bash
sudo systemctl restart portfolio.service
```

Remember to remove debug mode after troubleshooting.

---

## File Checksums

After deployment, verify file integrity:

```bash
# Verify application files
sudo sha256sum /opt/portfolio/app.py /opt/portfolio/requirements.txt

# Verify configuration files
sudo sha256sum /etc/systemd/system/portfolio.service /etc/nginx/conf.d/portfolio.conf
```
