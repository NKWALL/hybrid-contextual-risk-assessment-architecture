"""
FastAPI 主程式 - 智慧治理入口
"""

import warnings

# === 過時警告終極抑制 ===
# Filter 機制會被某些 SDK（例如 Appwrite）內部呼叫 simplefilter('always') 覆寫。
# 直接 monkey-patch warnings.showwarning，從顯示層攔截，filter 怎麼設都不影響。
# 只擋 DeprecationWarning，其他種類的 warning（UserWarning / RuntimeWarning）正常顯示。
_original_showwarning = warnings.showwarning
def _silence_deprecation(message, category, filename, lineno, file=None, line=None):
    if issubclass(category, DeprecationWarning):
        return
    _original_showwarning(message, category, filename, lineno, file, line)
warnings.showwarning = _silence_deprecation

# 保留既有 filter 當第二道防線（filter 沒被覆寫的情況下也有效）
warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import risk_detection

app = FastAPI(
    title="AI Dating Safety - 風險檢測 API",
    version="0.1.0"
)

# CORS（允許 Flutter 呼叫）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 註冊路由
app.include_router(
    risk_detection.router,
    prefix="/api/v1/risk",
    tags=["風險檢測"]
)

@app.get("/")
async def root():
    return {
        "service": "AI Dating Safety API",
        "version": "0.1.0",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}
