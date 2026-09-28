"""
MySQL 資料庫連線工具 - 修正空密碼連線問題
"""

import mysql.connector
import os
from dotenv import load_dotenv

# 重新載入環境變數
load_dotenv(override=True)

def get_mysql_connection():
    try:
        # 讀取並去除前後空格
        raw_pw = os.getenv('MYSQL_PASSWORD', '')
        
        # 關鍵修正：如果是空字串或 None，則設為 None 告訴驅動程式不用密碼
        password = raw_pw.strip() if raw_pw else None
        if not password: 
            password = None

        conn = mysql.connector.connect(
            host=os.getenv('MYSQL_HOST', 'localhost'),
            user=os.getenv('MYSQL_USER', 'root'),
            password=password, # 傳入 None 則對應 (using password: NO)
            database=os.getenv('MYSQL_DATABASE', 'dating_safety'),
            charset='utf8mb4',
            collation='utf8mb4_general_ci'
        )
        return conn
    except mysql.connector.Error as err:
        # 將警告標記為 [ Database Error ]
        print(f"   [ Database Error ] 無法連線至 MySQL: {err}")
        return None
