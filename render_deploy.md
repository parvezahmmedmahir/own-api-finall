# Deploying PyQuotex Headless API to Render

This guide explains how to deploy your Quotex API to Render so you can access live market data, assets, and balance from any of your other projects.

## 📁 Repository Structure
Ensure your GitHub repository (`own-api-finall`) contains these essential files:
- `dashboard_server.py`: The main API script (FastAPI).
- `requirements.txt`: List of Python dependencies.
- `Procfile`: Command for Render to start the server.
- `pyquotex/`: The core logic folder.

---

## 🚀 Deployment Steps (Render.com)

### 1. Create a New Web Service
1. Log in to [Render.com](https://render.com).
2. Click **New +** and select **Web Service**.
3. Connect your GitHub repository: `own-api-finall`.

### 2. Configure Build & Start
- **Runtime**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn -w 1 -k uvicorn.workers.UvicornWorker dashboard_server:app --bind 0.0.0.0:$PORT`

### 3. Add Environment Variables (IMPORTANT)
Go to the **Environment** tab in your Render dashboard and add these two keys:
1. `QUOTEX_EMAIL`: Your Quotex email address.
2. `QUOTEX_PASSWORD`: Your Quotex password.

---

## 🔗 Using Your API Endpoints

Once deployed, your API will have a live URL (e.g., `https://own-api-finall.onrender.com`).

### 1. Check if Online
- **URL**: `GET /`
- **Use**: Check if the server is running.

### 2. Get Available Assets
- **URL**: `GET /api/assets`
- **Returns**: JSON list of all symbols (e.g., `EURUSD_otc`) and their Open/Closed status.

### 3. Get Account Balance
- **URL**: `GET /api/balance`
- **Returns**: Current practice balance.

### 4. Real-time Market Data (WebSocket)
To get live price movement (ticks) and candle history:
- **Connection**: `wss://own-api-finall.onrender.com/ws`
- **Message format to switch market**:
  ```json
  {
    "type": "switch",
    "asset": "EURUSD_otc",
    "period": 60
  }
  ```

---

## 💻 Git Commands to Push Now
Run these in your terminal to update your repository:

```bash
git add .
git commit -m "Transform to headless API for Render deployment"
git remote add origin git@github.com:parvezahmmedmahir/own-api-finall.git
git branch -M main
git push -u origin main
```
