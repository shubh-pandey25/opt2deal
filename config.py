import os
from dotenv import load_dotenv
from groq import Groq
from openai import OpenAI
from typing import Union

# Load environment variables from .env file
load_dotenv()

# Toggles for Ollama vs Groq
USE_OLLAMA = os.getenv("USE_OLLAMA", "false").lower() == "true"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3").strip()

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

def get_available_ollama_models() -> list:
    import urllib.request
    import json
    try:
        # Query Ollama's local tags endpoint to see what models are pulled
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1.0) as response:
            data = json.loads(response.read().decode())
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []

# Set DEFAULT_MODEL based on configuration
if USE_OLLAMA:
    available = get_available_ollama_models()
    # Normalize model tags (check matching prefix or exact match)
    if OLLAMA_MODEL in available: 
        DEFAULT_MODEL = OLLAMA_MODEL
    elif f"{OLLAMA_MODEL}:latest" in available:
        DEFAULT_MODEL = f"{OLLAMA_MODEL}:latest"
    elif available: 
        # Fall back to the first available local model (e.g. llama3.2:latest)
        DEFAULT_MODEL = available[0]
    else:
        DEFAULT_MODEL = OLLAMA_MODEL
else:
    DEFAULT_MODEL = GROQ_MODEL
   
# Dynamic Fallback Model Pool
fallback_str = os.getenv("GROQ_FALLBACK_MODELS", "llama-3.3-70b-versatile,llama-3.1-8b-instant,gemma2-9b-it,mixtral-8x7b-32768,llama-3.2-11b-vision-preview,llama-3.2-3b-preview")
GROQ_FALLBACK_MODELS = [m.strip() for m in fallback_str.split(",") if m.strip()]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

def get_groq_client() -> Union[Groq, OpenAI]:
    """
    Initializes and returns either the Groq client or OpenAI (Ollama) client
    based on the USE_OLLAMA environment variable.
    """
    if USE_OLLAMA:
        return OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama"  # Ollama doesn't require a real key, but OpenAI client needs a non-empty string
        )
    
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is not set. Please set the GROQ_API_KEY environment variable "
            "or configure it in your .env file, or set USE_OLLAMA=true to run locally."
        )
    return Groq(api_key=GROQ_API_KEY)


def get_llm_client_and_model(task_type: str = "general") -> tuple:
    """
    Returns (client, model_name) for either Gemini, Groq, or Ollama.
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
        client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=gemini_key
        )
        return client, model

    if USE_OLLAMA:
        client = OpenAI(
            base_url=OLLAMA_BASE_URL,
            api_key="ollama"
        )
        return client, OLLAMA_MODEL

    # Fall back to Groq
    if not GROQ_API_KEY:
        raise ValueError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured.")
    
    client = Groq(api_key=GROQ_API_KEY)
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    return client, model
