from notion_client import Client
from config import NOTION_TOKEN, DATABASE_COURS_ID

notion = Client(auth=NOTION_TOKEN)

def fetch_courses():
    response = notion.databases.query(database_id=DATABASE_COURS_ID)
    return response["results"]
