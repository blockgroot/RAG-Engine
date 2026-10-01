# LettuceDetect answer check

The endpoint behind `RAG_AUDIT_BACKEND=lettuce`. It flags the parts of an
answer the retrieved context does not support. See `app/rag/audit.py` in the
main repo.

## Deploy

**Not free, and not enabled.** Hugging Face made CPU Spaces a paid plan, and
Docker Spaces are paid. A free Gradio Space runs on ZeroGPU, which is the wrong
hardware for this checker. The app in this folder is ready for a **paid private
CPU Space**. Until that exists, `RAG_AUDIT_ENABLED` stays off. Product status:
`PRODUCT_STATUS.md` (answer fact-checker).

`app.py` starts the checker on port 7860.

1. On huggingface.co: **New Space → Gradio → Blank**, hardware **a paid CPU
   Space**, visibility **Private**. It must be private: every request carries a
   tenant's retrieved documents.
2. Space → **Files → Add file → Upload files**: `app.py` and
   `requirements.txt` from this folder. Keep the README Hugging Face created
   (its header holds the Gradio version the Space needs).
3. Create a **read** access token (Settings → Access Tokens). A private Space
   answers only requests carrying one.
4. Check it: `curl -H "Authorization: Bearer hf_..." https://<you>-<space>.hf.space/health`
5. On the Hand-Book Render service, set:
   ```
   RAG_AUDIT_ENABLED=true
   RAG_AUDIT_BACKEND=lettuce
   RAG_AUDIT_LETTUCE_URL=https://<you>-<space>.hf.space
   RAG_AUDIT_LETTUCE_TOKEN=hf_...
   ```

## Known limits

- **A sleeping Space skips the check.** A request that times out (8s) does not
  block the answer; the audit is skipped. Measure CPU latency on the paid Space
  before turning `RAG_AUDIT_ENABLED` on.
- **The model downloads on each cold start** (~1.6GB, HF to HF, so fast, but
  the first request after a wake is slow).
- **CPU speed is not measured yet.** If the large model is too slow, set the
  Space variable `LETTUCE_MODEL=KRLabsOrg/lettucedect-base-modernbert-en-v1`
  (Space → Settings → Variables and secrets) and restart it.
- **English only.** Other languages need a different LettuceDetect checkpoint.
