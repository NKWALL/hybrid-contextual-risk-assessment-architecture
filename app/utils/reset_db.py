import os
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query

load_dotenv()

def reset_collections():
    client = Client()
    client.set_endpoint(os.getenv('APPWRITE_ENDPOINT'))
    client.set_project(os.getenv('APPWRITE_PROJECT_ID'))
    client.set_key(os.getenv('APPWRITE_API_KEY'))
    db = Databases(client)
    db_id = os.getenv('APPWRITE_DB_ID')

    # 所有「操作型資料」collection。
    # 三個記憶／回饋相關的表過去未被清除，會造成評估污染：
    #   conversation_summaries    L1 摘要（親密度指標）殘留會蓋掉評估時種入的值
    #   relationship_metrics      L2 關係指標同上
    #   guardrail_context_reviews 背景判斷結果會餵進 feedback_signal，
    #                             舊的 concerning 會讓後續風險被莫名放大
    target_collections = [
        "messages",
        "temporal_features",
        "risk_analysis_logs_",
        "risk_state_history",
        "intervention_logs",
        "conversations",
        "conversation_summaries",
        "relationship_metrics",
        "guardrail_context_reviews",
    ]

    for coll_id in target_collections:
        print(f"Cleaning collection: {coll_id}...")
        try:
            while True:
                # 修正: 使用物件屬性 .documents 存取回傳結果
                response = db.list_documents(db_id, coll_id, queries=[Query.limit(100)])
                documents = response.documents
                
                if not documents:
                    break
                
                for doc in documents:
                    # 修正: 使用 .id 存取文件 ID
                    db.delete_document(db_id, coll_id, doc.id)
            
            print(f"Collection {coll_id} cleared successfully.")
        except Exception as e:
            # 針對不存在的表優化提示
            if "not found" in str(e):
                print(f"Skip: Collection {coll_id} does not exist.")
            else:
                print(f"Error clearing {coll_id}: {e}")

if __name__ == "__main__":
    confirm = input("Confirm clearing all ChatLog data? (y/n): ")
    if confirm.lower() == 'y':
        reset_collections()
    else:
        print("Reset cancelled.")
