import openai
import os
from dotenv import load_dotenv
import yaml

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

def classify_ticket(text: str) -> str:
    # Use OpenAI's API to classify the ticket
    response = openai.Completion.create(
        model="text-davinci-003",
        prompt=f"Please classify the following ticket：{text}",
        max_tokens=50
    )
    return response.choices[0].text.strip()
