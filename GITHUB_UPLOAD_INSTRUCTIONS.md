# 📤 Upload to GitHub - Complete Instructions

## Repository URL
**https://github.com/tech920/motor-claim-decision-api**

---

## Option 1: Install Git and Use Command Line (Recommended)

### Step 1: Install Git

1. Download Git: https://git-scm.com/download/win
2. Run installer (use default options)
3. Restart PowerShell/Command Prompt

### Step 2: Initialize and Push

Open PowerShell or Command Prompt and run:

```bash
cd D:\Motorclaimde
git init
git add .
git commit -m "Initial commit: Motor Claim Decision API (TP + CO)"
git remote add origin https://github.com/tech920/motor-claim-decision-api.git
git branch -M main
git push -u origin main
```

**If asked for credentials:**
- Username: `tech920`
- Password: Use **Personal Access Token** (not your password)
  - Get token: https://github.com/settings/tokens
  - Click "Generate new token (classic)"
  - Select `repo` scope
  - Copy token and paste as password

---

## Option 2: Use GitHub Desktop (Easiest)

### Step 1: Install GitHub Desktop

1. Download: https://desktop.github.com/
2. Install and sign in with your GitHub account

### Step 2: Add Repository

1. Open GitHub Desktop
2. Click **File** → **Add Local Repository**
3. Browse to: `D:\Motorclaimde`
4. Click "Add Repository"

### Step 3: Publish

1. Click **Publish repository** button
2. Repository name: `motor-claim-decision-api`
3. Make sure "Keep this code private" is checked (if you want private)
4. Click **Publish Repository**

---

## Option 3: Use GitHub Web Interface

### Step 1: Create Repository (if not created)

1. Go to: https://github.com/new
2. Repository name: `motor-claim-decision-api`
3. Choose: Private (recommended)
4. **DO NOT** initialize with README
5. Click **Create repository**

### Step 2: Upload Files via Web

1. Go to your repository: https://github.com/tech920/motor-claim-decision-api
2. Click **"uploading an existing file"** link
3. Drag and drop all files from `D:\Motorclaimde`:
   - `unified_api_server.py`
   - `requirements.txt`
   - `README.md`
   - `.gitignore`
   - All files in `MotorclaimdecisionlinuxTP/` folder
   - All files in `MotorclaimdecisionlinuxCO/` folder
4. Scroll down, enter commit message: "Initial commit: Motor Claim Decision API"
5. Click **Commit changes**

---

## Option 4: Use Cursor Built-in Git

### Step 1: Open in Cursor

1. Open **Cursor**
2. Click **File** → **Open Folder**
3. Select: `D:\Motorclaimde`

### Step 2: Initialize Git

1. Press `Ctrl+Shift+P`
2. Type: `Git: Initialize Repository`
3. Select the folder

### Step 3: Stage and Commit

1. Click **Source Control** icon (left sidebar) or press `Ctrl+Shift+G`
2. Click **+** next to "Changes" to stage all files
3. Enter commit message: "Initial commit: Motor Claim Decision API"
4. Click **✓ Commit**

### Step 4: Push to GitHub

1. Click **...** (three dots) in Source Control panel
2. Click **Push to...**
3. Enter: `https://github.com/tech920/motor-claim-decision-api.git`
4. Enter credentials when prompted

---

## ✅ Verify Upload

After uploading, check:
1. Go to: https://github.com/tech920/motor-claim-decision-api
2. Verify structure:
   - ✅ `unified_api_server.py` in root
   - ✅ `MotorclaimdecisionlinuxTP/` folder with 9 files
   - ✅ `MotorclaimdecisionlinuxCO/` folder with 9 files
   - ✅ `README.md`, `requirements.txt`, `.gitignore`

---

## 🤖 Connect Cursor AI

After code is on GitHub:

1. Open **Cursor**
2. Press `Ctrl+Shift+P`
3. Type: `Git: Clone`
4. Paste: `https://github.com/tech920/motor-claim-decision-api.git`
5. Select destination folder
6. Click **Open**

Then press `Ctrl+L` to chat with AI about your code!

---

## 📝 Quick Reference

**Repository:** https://github.com/tech920/motor-claim-decision-api

**Local Path:** `D:\Motorclaimde`

**Total Files:** 23 files (5 main + 9 TP + 9 CO)

---

## 🆘 Troubleshooting

| Problem | Solution |
|---------|----------|
| Git not found | Install Git from https://git-scm.com/download/win |
| Authentication fails | Use Personal Access Token, not password |
| Files not showing | Make sure you added all files (including subdirectories) |
| Permission denied | Check repository permissions on GitHub |

---

**Choose the method that works best for you!** 🚀

