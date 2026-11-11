FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY cli ./cli
# 설명문 파이프라인(python -m cli.expository)의 프롬프트·라우트. 실행 결과는 runs/ 에 쓰므로 그 폴더만 appuser 소유로 만든다
COPY pipeline/expository ./pipeline/expository

RUN useradd --create-home --uid 1000 appuser && install -d -o appuser /srv/grammars /srv/pipeline/expository/runs
USER appuser

# 문법은 wheel 에 없고 첫 사용 때 GitHub 에서 받는다. 이미지에 넣어 실행 중 네트워크와 쓰기 권한에 기대지 않는다(G-24).
# 목록은 app/services/code_graph.py 의 GRAMMARS 와 같아야 한다 (tests/test_dockerfile.py)
ENV TREE_SITTER_LANGUAGE_PACK_CACHE_DIR=/srv/grammars
RUN python -c "from tree_sitter_language_pack import prefetch; prefetch(['kotlin', 'python', 'tsx', 'typescript'])"

EXPOSE 8000

# 워커는 1개로 고정한다. KIS WebSocket 매니저와 인프로세스 캐시가 프로세스 하나를 전제로 한다.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
