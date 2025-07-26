import openai
from config import OPENAI_API_KEY

openai.api_key = OPENAI_API_KEY


def generate_flashcard(text):
    """Placeholder for future flashcard generation."""
    pass


def ask_question(question: str) -> str:
    """Send a question to the OpenAI API and return the response text."""
    if not OPENAI_API_KEY:
        return "Clé API OpenAI manquante."

    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": question}],
            temperature=0.2,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"Erreur: {exc}"
