#!/bin/bash

# Deployment Script for Motor Claim Decision API
# Deploys to /opt/Motorclaimdecision_main/

TARGET_DIR="/opt/Motorclaimdecision_main"
SERVICE_FILE="motorclaim.service"
SYSTEMD_DIR="/etc/systemd/system"

echo "=================================================="
echo "Motor Claim Decision API Deployment Script"
echo "=================================================="

# Check root privileges
if [ "$EUID" -ne 0 ]; then
  echo "❌ Please run as root (sudo)"
  exit 1
fi

# Create target directory
echo "📂 Creating directory: $TARGET_DIR"
mkdir -p "$TARGET_DIR"
mkdir -p "$TARGET_DIR/logs"

# Copy files
echo "📦 Copying files to $TARGET_DIR..."
cp -r ./* "$TARGET_DIR/"

# Set permissions
echo "🔒 Setting permissions..."
chown -R root:root "$TARGET_DIR"
chmod -R 755 "$TARGET_DIR"

# Install dependencies (optional, assumes already installed in environment)
# echo "📦 Installing Python dependencies..."
# pip3 install -r "$TARGET_DIR/requirements.txt"

# Install systemd service
echo "⚙️ Installing systemd service..."
cp "$TARGET_DIR/$SERVICE_FILE" "$SYSTEMD_DIR/"
systemctl daemon-reload

# Enable and start service
echo "🚀 Starting service..."
systemctl enable motorclaim
systemctl restart motorclaim

# Check status
echo "🔍 Checking status..."
sleep 2
systemctl status motorclaim --no-pager

echo "=================================================="
echo "✅ Deployment Complete!"
echo "   Server running on port 5000"
echo "   Logs: $TARGET_DIR/logs/"
echo "=================================================="
