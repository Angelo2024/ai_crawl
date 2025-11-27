import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

if DEEPSEEK_API_KEY:
    print("✅ DeepSeek API Key loaded successfully.")
else:
    print("❌ DEEPSEEK_API_KEY not found. Please check your .env file or environment variables.")

