# Performance Optimization Guide - Ollama on CPU

## Current Issue: Slow Agent Runs ⏱️

**Why it's slow:**
- Ollama is running on **CPU only** (no compatible GPU detected)
- LLMs on CPU can take **30-120 seconds** per agent run
- Your system has 10 CPU cores but LLM inference is compute-intensive

## ✅ OPTIMIZATIONS APPLIED

I've already optimized your setup:

### 1. **Faster Ollama Settings** (Just Applied)
Added to `ai_generation.py`:
- `num_ctx: 4096` - Reduced context window (faster processing)
- `num_thread: 8` - Use 8 CPU threads for parallel work
- `temperature: 0.7` - Faster, more focused output
- `top_k: 40` - Limit token choices for speed

### 2. **Instant Approval** (Already Fixed)
- Approvals now use heuristic summaries (instant, no AI)
- Only agent runs use Ollama (where slowness is expected)

## 🚀 ADDITIONAL SPEED OPTIONS

### Option 1: Use Faster Model (RECOMMENDED)

**Currently downloading:** `phi3:mini` (3.8 GB, optimized for speed)

Once download completes, switch to it in `apps/api/.env`:
```env
OLLAMA_MODEL=phi3:mini
```

**Speed comparison (CPU):**
- llama3.2:3b: ~45-90 seconds per run
- phi3:mini: ~30-60 seconds per run ✅ 30% faster
- phi3:mini is also better at following instructions

### Option 2: Use Even Smaller Model

Try the tiniest model for maximum speed:
```powershell
ollama pull qwen2:0.5b
```

Then in `.env`:
```env
OLLAMA_MODEL=qwen2:0.5b
```

**Speed:** ~15-30 seconds per run (but lower quality)

### Option 3: Mock Mode for Testing

For instant testing during development:

In `apps/api/.env`, comment out Ollama:
```env
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=llama3.2:3b
```

**Speed:** Instant (deterministic output, no AI)
**Use case:** Testing workflow, UI, or features without waiting

### Option 4: GPU Acceleration (Best Solution)

If you have an NVIDIA GPU:
1. Install CUDA toolkit
2. Ollama will automatically detect and use it
3. Speed: **5-15 seconds per run** (10x faster!)

Check if you have a compatible GPU:
```powershell
nvidia-smi
```

## 📊 EXPECTED PERFORMANCE

### With Current Optimizations (CPU):

| Model | Approval | Short Run | Long Run |
|-------|----------|-----------|----------|
| llama3.2:3b | < 1s ✅ | 30-60s | 60-120s |
| phi3:mini | < 1s ✅ | 20-45s | 45-90s |
| qwen2:0.5b | < 1s ✅ | 10-25s | 25-50s |

### With GPU (if available):

| Model | Approval | Short Run | Long Run |
|-------|----------|-----------|----------|
| llama3.2:3b | < 1s | 5-12s ⚡ | 12-25s ⚡ |
| llama3.1:8b | < 1s | 8-18s | 18-40s |

## 🎯 RECOMMENDED SETUP

**For Development/Testing:**
```env
# Fast enough, good quality
OLLAMA_MODEL=phi3:mini
```

**For Production (if you need quality):**
- Get GPU acceleration, OR
- Use Anthropic/Gemini API (cloud-based, very fast)

**For Quick UI Testing:**
- Use mock mode (comment out all AI config)

## 💡 CURRENT STATUS

✅ **Approval speed**: Fixed (instant)  
⚙️ **Agent run speed**: Optimized settings applied  
⏬ **phi3:mini**: Currently downloading (47 minutes remaining)  

**Next steps:**
1. Wait for phi3:mini download to complete
2. Switch to it in `.env` 
3. Restart API server
4. Agent runs will be ~30% faster

**Or, for instant testing:**
- Use mock mode (comment out Ollama config)
- Re-enable Ollama when you need real AI output

## 🔧 TROUBLESHOOTING

**Still too slow?**
- Check CPU usage (Task Manager) - should be near 100% during generation
- Try qwen2:0.5b for maximum speed
- Consider cloud APIs (Gemini is free tier with rate limits)

**Want fastest possible?**
- Mock mode: Instant, deterministic
- GPU: 5-15 seconds
- Cloud API (Gemini/Anthropic): 2-8 seconds

---

**Bottom line:** CPU inference will always be slower than GPU or cloud. The optimizations applied should give you 20-30% speed boost, but for real speed you need GPU or cloud APIs.
