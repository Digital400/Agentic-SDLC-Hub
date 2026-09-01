"""
Quick test script to verify Ollama integration.
Run this after the llama3.1:8b model finishes downloading.
"""

import ollama


def test_ollama_connection():
    """Test that Ollama is running and has models available."""
    print("=" * 60)
    print("Ollama Integration Test")
    print("=" * 60)
    
    ollama_url = "http://localhost:11434"
    
    print(f"\n1. Testing connection to Ollama at {ollama_url}")
    try:
        client = ollama.Client(host=ollama_url)
        response = client.list()
        print(f"   ✅ Connected to Ollama successfully!")
        
        models = response.get('models', [])
        if models:
            print(f"\n2. Available models ({len(models)} found):")
            for model in models:
                model_name = model.get('name', 'unknown')
                size = model.get('size', 0) / (1024**3)  # Convert to GB
                modified = model.get('modified_at', 'unknown')
                print(f"   ✅ {model_name}")
                print(f"      Size: {size:.2f} GB")
                print(f"      Modified: {modified}")
                print()
        else:
            print(f"\n2. ⚠️  No models found!")
            print("   Download a model with:")
            print("   ollama pull llama3.1:8b")
        
        print("=" * 60)
        print("\n✅ Ollama is ready to use!")
        print("\nYour Agentic-SDLC-Hub will automatically use Ollama when:")
        print("  - ANTHROPIC_API_KEY is not set")
        print("  - GEMINI_API_KEY is not set")
        print("  - Ollama server is running")
        print("  - At least one model is available")
        print("\n" + "=" * 60)
        return True
        
    except Exception as e:
        print(f"   ❌ Cannot connect to Ollama: {e}")
        print("\n💡 Troubleshooting:")
        print("   1. Make sure Ollama is running:")
        print("      ollama serve")
        print("   2. Check if the server is listening on the right port:")
        print("      http://localhost:11434")
        print("   3. Download a model if needed:")
        print("      ollama pull llama3.1:8b")
        print("\n" + "=" * 60)
        return False


if __name__ == "__main__":
    test_ollama_connection()
