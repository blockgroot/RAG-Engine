---
title: Handbook answer check
sdk: docker
app_port: 7860
pinned: false
---

# LettuceDetect answer check

The endpoint behind `RAG_AUDIT_BACKEND=lettuce`. It flags the parts of an
answer the retrieved context does not support. See `app/rag/audit.py` in the
main repo.

## Deploy (free, nothing runs locally)

1. On huggingface.co: **New Space → Docker → Blank**, hardware **CPU basic
   (free)**, visibility **Private**. It must be private: every request carries a
   tenant's retrieved documents.
2. Push this folder's three files to the Space repo:
   ```bash
   git clone https://huggingface.co/spaces/<you>/<space> && cd <space>
   cp <repo>/deploy/lettucedetect-space/{app.py,Dockerfile,README.md} .
   git add . && git commit -m "answer check" && git push
   ```
3. Create a **read** access token (Settings → Access Tokens). A private Space
   answers only requests carrying one.
4. On the Hand-Book Render service, set:
   ```
   RAG_AUDIT_ENABLED=true
   RAG_AUDIT_BACKEND=lettuce
   RAG_AUDIT_LETTUCE_URL=https://<you>-<space>.hf.space
   RAG_AUDIT_LETTUCE_TOKEN=hf_...
   ```
5. Check it: `curl -H "Authorization: Bearer hf_..." https://<you>-<space>.hf.space/health`

## Known limits

- **Free Spaces sleep after 48h with no traffic.** A request to a sleeping
  Space times out (8s), which SKIPS the check, so answers keep flowing.
- **CPU speed is not measured yet.** If the large model is too slow, rebuild
  with `--build-arg LETTUCE_MODEL=KRLabsOrg/lettucedect-base-modernbert-en-v1`
  (Space → Settings → Variables).
- **English only.** Other languages need a different LettuceDetect checkpoint.
