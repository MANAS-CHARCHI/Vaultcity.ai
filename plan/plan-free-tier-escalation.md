# Plan: Cloudflare Workers AI Vision & OpenRouter Free-Tier Cascade

This roadmap details the implementation plan for:
1. **Cloudflare Workers AI Vision Integration:** Utilizing Cloudflare's serverless GPUs and **10,000 free daily Neurons** for raster images and photocopies.
2. **OpenRouter Cascading Fallback Hierarchy:** Querying **100% FREE frontier models first** (e.g. `llama-3.3-70b-instruct:free`, `gemini-2.0-flash-exp:free`), and seamlessly falling back to paid **Qwen 2.5-72B** only when free tier rate limits (HTTP 429) are encountered.

---

## 1. Cascading Model Architecture

```mermaid
flowchart TD
    UserQuery["User Chat / RAG Synthesis"] --> Router["OpenRouter Cascading Client"]

    subgraph Tier1 ["Tier 1: Free Big Models ($0.00 / 0 Cost)"]
        LlamaFree["meta-llama/llama-3.3-70b-instruct:free"]
        GeminiFree["google/gemini-2.0-flash-exp:free"]
    end

    subgraph Tier2 ["Tier 2: Paid High-Context Frontier (~$0.23 / 1M)"]
        Qwen72B["qwen/qwen-2.5-72b-instruct\n(Rock-solid uptime, no rate limits)"]
    end

    subgraph Tier3 ["Tier 3: Ultra-Fast Micro-Tier (~$0.04 / 1M)"]
        Qwen7B["qwen/qwen-2.5-7b-instruct\n(Fast summaries & memory updates)"]
    end

    subgraph Tier4 ["Tier 4: Offline Deterministic Fallback ($0.00)"]
        LocalGrounded["Grounded Document Chunks Excerpt\n(Zero API calls)"]
    end

    Router -->|1. Try Free 70B / Frontier| Tier1
    Tier1 -->|Success (200 OK)| ReturnAnswer["Return High-Confidence Answer ($0)"]
    
    Tier1 -->|Rate Limit / Quota (HTTP 429)| Tier2
    Tier2 -->|Success (200 OK)| ReturnPaid["Return High-Confidence Answer (< $0.001)"]
    
    Tier2 -->|API Error / Offline| Tier4
    Tier4 --> ReturnOffline["Return Exact Chunk Passages"]
```

---

## 2. Task 1: Cloudflare Workers AI Vision Plan

### Objective
Process raster images (`.png`, `.jpg`, `.jpeg`, `.webp`) and pure scanned photocopies with zero text using Cloudflare's free serverless GPUs, completely shielding the paid API tiers.

### Technical Implementation Details:
1. **Model:** `@cf/meta/llama-3.2-11b-vision-instruct` (Cloudflare-hosted Meta Llama 3.2 Vision).
2. **Endpoint:**
   ```http
   POST https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/@cf/meta/llama-3.2-11b-vision-instruct
   Authorization: Bearer {CLOUDFLARE_API_TOKEN}
   Content-Type: application/json
   ```
3. **Payload Structure:**
   ```json
   {
     "image": [/* raw image byte array or base64 */],
     "prompt": "Extract all readable text verbatim, identify document type, and provide a 2-sentence summary in JSON format."
   }
   ```
4. **Environment Variables Needed (in `.env`):**
   ```ini
   CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
   CLOUDFLARE_API_TOKEN=your_cloudflare_workers_ai_token
   CLOUDFLARE_VISION_MODEL=@cf/meta/llama-3.2-11b-vision-instruct
   ```
5. **Ingestion Gate Rule:**
   - Text PDFs (e-Aadhaar, Bank Statement, Resume) $\rightarrow$ Extracted via `pypdf`. **Zero vision calls.**
   - Only standalone images or photocopies where digital text $< 40$ chars $\rightarrow$ Cloudflare Workers AI.

---

## 3. Task 2: OpenRouter Free Big Models First $\rightarrow$ Qwen Fallback

### Objective
Maximize answers generated from **100% Free OpenRouter models** to keep total operational spend at **$0.00**, while guaranteeing high availability by cascading to **Qwen 2.5 72B** when free rate limits trigger.

### Recommended OpenRouter Free Model Registry:
1. **Primary Free Frontier:** `meta-llama/llama-3.3-70b-instruct:free`
   - 70 Billion parameters, state-of-the-art open reasoning, $0 cost.
2. **Secondary Free Frontier:** `google/gemini-2.0-flash-exp:free`
   - Massive 1M context window, exceptional speed, $0 cost.
3. **Tertiary Free Model:** `qwen/qwen-2.5-coder-32b-instruct:free`
   - 32 Billion parameters, exceptional structured output, $0 cost.
4. **Paid Fallback Guarantee:** `qwen/qwen-2.5-72b-instruct`
   - $0.23/1M tokens (effectively free for personal use), guaranteed 99.9% uptime, no free tier rate limit queues.

### Cascading Client Logic (`openrouter_client.py`):
```python
FREE_MODEL_TIER = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
]
PAID_FALLBACK_MODEL = "qwen/qwen-2.5-72b-instruct"

async def call_cascading_chat(messages, system_prompt=None):
    # 1. Loop through Free Tier models first
    for model in FREE_MODEL_TIER:
        try:
            resp, token_info = await call_model_api(model, messages, system_prompt)
            if token_info["status"] == "success" and resp:
                logger.info("Answered via Free Model: %s ($0.00)", model)
                return resp, token_info
            elif "429" in token_info.get("status", ""):
                logger.info("Free model %s rate-limited; cascading to next...", model)
                continue
        except Exception:
            continue

    # 2. If all free models are busy/rate-limited, fallback to rock-solid Qwen 2.5 72B
    logger.info("Cascading to Paid Tier: %s", PAID_FALLBACK_MODEL)
    return await call_model_api(PAID_FALLBACK_MODEL, messages, system_prompt)
```

---

## 4. Implementation Steps (When Ready to Enable)

1. **Step 1:** Configure `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in `.env`.
2. **Step 2:** Configure `OPENROUTER_API_KEY` in `.env`.
3. **Step 3:** Activate the `FREE_MODEL_TIER` cascade list in `openrouter_client.py`.
4. **Step 4:** Deploy and test with sample document uploads and complex chat queries.
