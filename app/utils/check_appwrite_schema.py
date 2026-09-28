import os
import json
from appwrite.client import Client
from dotenv import load_dotenv

load_dotenv()

# 連線資訊從環境變數讀取
ENDPOINT = os.getenv('APPWRITE_ENDPOINT')
PROJECT_ID = os.getenv('APPWRITE_PROJECT_ID')
API_KEY = os.getenv('APPWRITE_API_KEY')
DB_ID = os.getenv('APPWRITE_DB_ID')

def dump_schema_raw():
    client = Client()
    client.set_endpoint(ENDPOINT)
    client.set_project(PROJECT_ID)
    client.set_key(API_KEY)

    schema_dump = {"database_id": DB_ID, "collections": []}

    try:
        # 1. 獲取所有 Collections
        response = client.call('get', f'/databases/{DB_ID}/collections')
        collections = response.get('collections', [])

        for col in collections:
            col_id = col['$id']
            print(f"Checking Collection: {col_id}...")
            
            # 2. 獲取該 Collection 的 Attributes
            attr_response = client.call('get', f'/databases/{DB_ID}/collections/{col_id}/attributes')
            
            # 3. 獲取該 Collection 的 Indexes
            index_response = client.call('get', f'/databases/{DB_ID}/collections/{col_id}/indexes')
            
            col_info = {
                "name": col['name'],
                "id": col_id,
                "attributes": attr_response.get('attributes', []),
                "indexes": index_response.get('indexes', [])
            }
            schema_dump["collections"].append(col_info)

        # 將結果存成 JSON 檔案
        dump_path = os.path.join(os.getcwd(), 'appwrite_schema_dump.json')
        with open(dump_path, 'w', encoding='utf-8') as f:
            json.dump(schema_dump, f, indent=2, ensure_ascii=False)

        print(f"\nSuccess! Schema dumped to: {dump_path}")
        
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    dump_schema_raw()
