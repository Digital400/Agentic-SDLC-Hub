# Ollama Integration Guide

## Overview
Ollama has been successfully integrated into your Agentic-SDLC-Hub project. Ollama allows you to run Large Language Models (LLMs) locally on your machine for **completely free** with no API keys required.

## Current Status

✅ **Ollama installed** (version 0.6.3)  
✅ **Ollama server running** on http://localhost:11434  
🔄 **Model downloading** - llama3.1:8b (4.9 GB, ~2-3 hours depending on connection)  
✅ **Python client installed** - ollama==0.4.6  
✅ **Code integration complete** - AI generation service updated

## Provider Priority

The system now supports four AI providers in this priority order:

1. **Anthropic Claude** (if `ANTHROPIC_API_KEY` is set)
2. **Google Gemini** (if `GEMINI_API_KEY` is set)
3. **Ollama** (if running locally, no API key needed) ⭐ **NEW**
4. **Mock** (deterministic fallback)

## Configuration

### Environment Variables (apps/api/.env)

```env
# Ollama configuration (optional, these are the defaults)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
```

If you don't set these variables, the defaults will be used automatically.

## Available Models

You can use different models with Ollama. Here are some popular options:

### Small & Fast (< 5GB)
- `llama3.1:8b` (4.9 GB) - **Currently downloading** ✓ Recommended
- `llama3.2:3b` (2.0 GB) - Smaller, faster
- `phi3:mini` (2.4 GB) - Good for code
- `mistral:7b` (4.1 GB) - Good general purpose

### Medium (5-10GB)
- `llama3.1:70b` (40 GB) - High quality, requires 48GB RAM
- `mixtral:8x7b` (26 GB) - Mixture of experts

### Specialized
- `codellama:7b` (3.8 GB) - Optimized for code
- `deepseek-coder:6.7b` (3.8 GB) - Code-focused

## How to Use

### Pull a Model
```powershell
# Current model (already downloading)
ollama pull llama3.1:8b

# Try a smaller, faster model
ollama pull llama3.2:3b

# Try a code-focused model
ollama pull codellama:7b
```

### List Downloaded Models
```powershell
ollama list
```

### Test a Model
```powershell
ollama run llama3.1:8b "Hello, what can you help me with?"
```

### Change the Model
Edit `apps/api/.env` and set:
```env
OLLAMA_MODEL=llama3.2:3b
```

Then restart your API server.

### Start/Stop Ollama Server

The Ollama server is currently running in your terminal. To manage it:

**Stop**: Press Ctrl+C in the terminal running `ollama serve`

**Start**: Run `ollama serve` in a new PowerShell terminal

**Windows Service**: Ollama can also run as a Windows service (starts automatically on boot)

## Using Ollama in Your Project

Once the model download completes, your Agentic-SDLC-Hub will automatically use Ollama if:
- No `ANTHROPIC_API_KEY` is set
- No `GEMINI_API_KEY` is set
- Ollama server is running
- A model is available

### Test the Integration

1. Make sure Ollama is running: `ollama list` should show your downloaded models
2. Start your API server: `uvicorn app.main:app --reload --port 8000` (from `apps/api`)
3. The system will automatically detect and use Ollama
4. Check the logs - you should see the active provider logged

## Benefits of Ollama

✅ **Completely Free** - No API costs, no rate limits  
✅ **Private** - All data stays on your machine  
✅ **Fast** - Local inference, no network latency  
✅ **Offline** - Works without internet (after model download)  
✅ **Flexible** - Easy to switch between models  

## Troubleshooting

### "Error: could not connect to Ollama"
- Make sure Ollama server is running: `ollama serve`
- Check if it's listening on the right port: http://localhost:11434

### "Model not found"
- Pull the model first: `ollama pull llama3.1:8b`
- List available models: `ollama list`

### Slow responses
- Try a smaller model like `llama3.2:3b`
- Check available RAM
- Consider using GPU if available (requires NVIDIA GPU with CUDA)

### Want to use GPU acceleration?
Ollama automatically detects and uses NVIDIA GPUs with CUDA support. Your current setup is using CPU (no compatible GPU detected).

## Next Steps

1. **Wait for model download to complete** (~2-3 hours for llama3.1:8b)
2. **Test Ollama**: `ollama run llama3.1:8b "Write a hello world program"`
3. **Restart your API server** to use Ollama
4. **Create an agent run** in your web UI - it will use Ollama automatically!

## Resources

- **Ollama Website**: https://ollama.ai
- **Model Library**: https://ollama.ai/library
- **Ollama GitHub**: https://github.com/ollama/ollama
- **Python Client Docs**: https://github.com/ollama/ollama-python

## Need Help?

- Check Ollama logs in the terminal where you ran `ollama serve`
- View API logs in your FastAPI console
- The integration logs the active provider on startup
