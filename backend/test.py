import os
import logging
# Force verbose logging to catch the raw endpoint response
logging.basicConfig(level=logging.DEBUG)

from app.utils.llm_client import LLMClient # Adjust path to your file layout

try:
    print("Testing custom client link to Pro model...")
    client = LLMClient()
    
    # Mirror the parameters causing the crash
    test_response = client.chat(
        messages=[{"role": "user", "content": "Ping"}],
        max_tokens=4096, # Test standard bounds
        caller_hint="diagnostic_test"
    )
    print("Success response:", test_response)
except Exception as e:
    print("\n❌ RAW EXCEPTION DETECTED:")
    import traceback
    traceback.print_exc()