import os
from openai import OpenAI

# Récupère la clé depuis l'ENV
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("⚠️ Clé API introuvable dans les variables d'environnement.")

client = OpenAI(api_key=api_key)

# Petit test de chat
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "Tu es un assistant utile et concis."},
        {"role": "user", "content": "Donne-moi une phrase de test pour vérifier l'API."}
    ]
)

print("Réponse GPT-4o :", response.choices[0].message.content)
