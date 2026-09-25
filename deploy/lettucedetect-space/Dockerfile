FROM python:3.11-slim

# HF Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH HF_HOME=/home/user/.cache/huggingface
WORKDIR /home/user/app

# CPU-only torch: the default wheel drags in ~2GB of CUDA the free tier cannot use.
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --user lettucedetect fastapi "uvicorn[standard]"

ARG LETTUCE_MODEL=KRLabsOrg/lettucedect-large-modernbert-en-v1
ENV LETTUCE_MODEL=$LETTUCE_MODEL
# Bake the weights into the image so a cold start does not download them.
RUN python -c "from lettucedetect.models.inference import HallucinationDetector as H; H(method='transformer', model_path='$LETTUCE_MODEL')"

COPY --chown=user app.py .
# --no-access-log: request bodies are tenant data; keep even paths out of the log.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", "--no-access-log"]
