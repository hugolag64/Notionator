from datetime import date
from notion_client import Client
from config import NOTION_TOKEN, DATABASE_COURS_ID

notion = Client(auth=NOTION_TOKEN)

def fetch_courses():
    response = notion.databases.query(database_id=DATABASE_COURS_ID)
    return response["results"]


def _get_property_of_type(obj, prop_type):
    """Return the first property of the given Notion block matching the type."""
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == prop_type:
            return prop
    return None


def get_course_title(course):
    """Extract the title from a course object."""
    title_prop = _get_property_of_type(course, "title")
    if not title_prop:
        return "Sans titre"
    parts = title_prop.get("title", [])
    if parts:
        return parts[0].get("plain_text", "Sans titre")
    return "Sans titre"


def get_course_due_date(course):
    """Extract the first date property from a course object."""
    date_prop = _get_property_of_type(course, "date")
    if not date_prop:
        return None
    date_val = date_prop.get("date")
    if date_val:
        return date_val.get("start")
    return None


def fetch_courses_due_today():
    """Return courses that have a date property matching today's date."""
    today = date.today().isoformat()
    due = []
    for course in fetch_courses():
        if get_course_due_date(course) == today:
            due.append(course)
    return due
